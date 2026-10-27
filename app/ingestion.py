from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from io import BytesIO
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import zipfile
import xml.etree.ElementTree as ET

from pypdf import PdfReader

from .config import Settings
from .providers import OpenAIProvider


@dataclass
class Unit:
    text: str
    locator: dict
    extraction: str


@dataclass
class Extracted:
    units: list[Unit]
    modality: str
    occurred_at: str | None = None


class ReadableHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self.hidden += 1
        if not self.hidden and tag in ("p", "div", "br", "li", "h1", "h2", "section"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def html_text(text: str) -> str:
    parser = ReadableHTML()
    parser.feed(text)
    return "".join(parser.parts).strip()


def transcript_units(segments) -> list[Unit]:
    if not isinstance(segments, list) or not 1 <= len(segments) <= 10_000:
        raise ValueError("Transcript needs 1–10,000 timed segments")
    units = []
    for i, segment in enumerate(segments):
        if not isinstance(segment, dict):
            raise ValueError("Each segment must be an object")
        start, end, text = segment.get("start"), segment.get("end"), segment.get("text")
        if (not isinstance(start, (int, float)) or not isinstance(end, (int, float))
                or not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end
                or not isinstance(text, str) or not text.strip()):
            raise ValueError("Segments need finite 0 <= start < end and non-empty text")
        speaker = segment.get("speaker")
        if speaker is not None and (not isinstance(speaker, str) or len(speaker) > 200):
            raise ValueError("Invalid speaker label")
        units.append(Unit(text, {"segment": i + 1, "start_seconds": start, "end_seconds": end,
                                 "speaker": speaker}, "transcript"))
    return units


def extract(data: bytes, filename: str, settings: Settings,
            provider: OpenAIProvider, allow_cloud: bool = False) -> Extracted:
    ext = Path(filename).suffix.lower()
    if ext in (".txt", ".md"):
        result = Extracted([Unit(data.decode("utf-8-sig"), {"document": 1}, "text")], "document")
    elif ext in (".html", ".htm"):
        result = Extracted([Unit(html_text(data.decode("utf-8-sig")), {"document": 1}, "html_text")], "web_export")
    elif ext == ".eml":
        msg = BytesParser(policy=policy.default).parsebytes(data)
        body = msg.get_body(preferencelist=("plain", "html"))
        if body is None:
            raise ValueError("Email has no readable body")
        text = body.get_content()
        if body.get_content_type() == "text/html":
            text = html_text(text)
        text = "\n".join(f"{key}: {msg.get(key, '')}" for key in ("From", "To", "Subject", "Date")) + "\n\n" + text
        date = None
        try:
            parsed = parsedate_to_datetime(str(msg["Date"]))
            if parsed.tzinfo is not None:
                date = parsed.isoformat()
        except (TypeError, ValueError, IndexError):
            pass
        result = Extracted([Unit(text, {"message_id": str(msg.get("Message-ID", ""))}, "email_text")], "email", date)
    elif ext == ".pdf":
        pdf = PdfReader(BytesIO(data))
        if pdf.is_encrypted:
            raise ValueError("Encrypted PDF unsupported; export an unencrypted copy")
        if len(pdf.pages) > 300:
            raise ValueError("PDF exceeds 300-page prototype limit")
        units = []
        total = 0
        for i, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            total += len(text)
            if total > settings.max_extracted_chars:
                raise ValueError("Extracted text exceeds prototype limit")
            if text.strip():
                units.append(Unit(text, {"page": i + 1}, "pdf_text"))
        # Partial scanned PDFs must not silently appear completely indexed.
        if len(units) != len(pdf.pages):
            raise ValueError("One or more PDF pages have no text; OCR the PDF before import")
        result = Extracted(units, "document")
    elif ext == ".docx":
        with zipfile.ZipFile(BytesIO(data)) as z:
            info = z.getinfo("word/document.xml")
            if info.file_size > 5_000_000:
                raise ValueError("DOCX document XML exceeds limit")
            root = ET.fromstring(z.read(info))
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        units = [Unit("".join(p.itertext()), {"paragraph": i + 1}, "docx_text")
                 for i, p in enumerate(root.findall(".//w:p", ns)) if "".join(p.itertext()).strip()]
        result = Extracted(units, "document")
    elif ext == ".json":
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError("Transcript JSON must be an object")
        result = Extracted(transcript_units(value.get("segments")), "meeting", value.get("occurred_at"))
    elif ext in (".png", ".jpg", ".jpeg", ".webp"):
        if allow_cloud:
            media = {".png": "image/png", ".webp": "image/webp"}.get(ext, "image/jpeg")
            text = provider.image(data, media, allow_cloud)
            method = "vision_generated_description"
        else:
            executable = shutil.which("tesseract")
            if executable is None:
                raise ValueError("Install Tesseract for local image OCR, or explicitly enable cloud vision")
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / ("input" + ext)
                path.write_bytes(data)
                try:
                    done = subprocess.run([executable, str(path), "stdout"], capture_output=True,
                                          check=True, timeout=30)
                    text = done.stdout.decode("utf-8")
                except (subprocess.SubprocessError, UnicodeError) as exc:
                    raise ValueError("Local OCR failed or timed out") from exc
            method = "ocr_text"
        result = Extracted([Unit(text, {"image": 1}, method)], "image")
    elif ext in (".mp3", ".wav", ".m4a", ".mp4", ".webm"):
        result = Extracted(transcript_units(provider.transcribe(data, filename, allow_cloud)), "meeting")
    else:
        raise ValueError("Unsupported extension; use TXT, MD, PDF, DOCX, EML, HTML, transcript JSON, image or audio")
    result.units = [u for u in result.units if u.text.strip()]
    if not result.units:
        raise ValueError("No readable text extracted")
    if sum(len(u.text) for u in result.units) > settings.max_extracted_chars:
        raise ValueError("Extracted text exceeds prototype limit")
    return result


def chunks(text: str, size: int = 1000, overlap: int = 150):
    if not 0 <= overlap < size:
        raise ValueError("Require 0 <= overlap < chunk size")
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            boundary = text.rfind("\n", start + size // 2, end)
            if boundary != -1:
                end = boundary + 1
        yield start, end, text[start:end]
        if end == len(text):
            break
        start = end - overlap


def event_candidates(text: str):
    # Rules are intentionally transparent and recall-limited. Never infer a speaker's identity.
    for line in text.splitlines():
        if re.search(r"\b(decided|agreed|approved)\b|\bdecision:", line, re.I):
            yield "decision", line
        elif re.search(r"\b(i will|i'll|we will)\b|\b(commitment|action item):", line, re.I):
            yield "commitment", line


def entities(text: str):
    # A mention graph, not inferred relationships between people.
    labels = set(re.findall(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", text))
    for label in labels:
        yield label.lower(), "email"
    topics = ("pricing", "deployment", "architecture", "budget", "deadline", "FastAPI", "PostgreSQL", "Qdrant")
    for topic in topics:
        if re.search(r"\b" + re.escape(topic) + r"\b", text, re.I):
            yield topic.lower(), "topic"
