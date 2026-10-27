from datetime import datetime, timezone
from io import BytesIO
import json
import zipfile
import shutil
from pathlib import Path
import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.ingestion import chunks, html_text, transcript_units
from app.temporal import window


def test_chunk_offsets_cover_entire_original_without_dropping_text():
    text = "alpha\n" * 1000 + "final line"
    parts = list(chunks(text))
    covered = set()
    for start, end, content in parts:
        assert content == text[start:end]
        covered.update(range(start, end))
    assert covered == set(range(len(text)))


def test_html_ignores_executable_content():
    result = html_text("<h1>Pricing</h1><script>secret script</script><style>hide</style><p>USD 99 &amp; tax</p>")
    assert "Pricing" in result and "99 & tax" in result
    assert "secret script" not in result and "hide" not in result


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract optional on host; included in Docker image")
def test_actual_local_screenshot_ocr_and_original_retrieval(client, upload):
    path = Path(__file__).parents[1]/"examples"/"deployment-screenshot.png"
    sid = upload(path.read_bytes(), path.name)
    r = client.post("/v1/ask", json={"question": "Find the screenshot with deployment architecture"})
    assert r.status_code == 200
    result = r.json()
    assert not result["abstained"]
    evidence = result["evidence"][0]
    assert evidence["source_id"] == sid and evidence["extraction"] == "ocr_text"
    assert evidence["modality"] == "image" and "FastAPI" in evidence["quote"]
    assert client.get(evidence["original_url"]).content == path.read_bytes()


def test_docx_paragraph_locator(client, upload):
    data = BytesIO()
    with zipfile.ZipFile(data, "w") as z:
        z.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>We agreed pricing is 99.</w:t></w:r></w:p></w:body></w:document>')
    sid = upload(data.getvalue(), "notes.docx")
    detail = client.get(f"/v1/sources/{sid}").json()
    assert json.loads(detail["units"][0]["locator"])["paragraph"] == 1
    assert "pricing" in detail["units"][0]["text"]


def test_email_date_and_message_locator(client, upload):
    data = b"From: bhanu@example.test\r\nTo: client@example.test\r\nSubject: Pricing decision\r\nDate: Fri, 18 Sep 2026 10:00:00 +0530\r\nMessage-ID: <demo@example.test>\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nWe agreed pricing is USD 99."
    sid = upload(data, "pricing.eml", when=None)
    detail = client.get(f"/v1/sources/{sid}").json()
    assert detail["occurred_at"] == "2026-09-18T04:30:00+00:00"
    assert detail["time_basis"] == "source_metadata"
    assert json.loads(detail["units"][0]["locator"])["message_id"] == "<demo@example.test>"


def test_pdf_page_locator(client, upload):
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 72 720 Td (We decided pricing is USD 99.) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    out = BytesIO()
    writer.write(out)
    sid = upload(out.getvalue(), "pricing.pdf")
    detail = client.get(f"/v1/sources/{sid}").json()
    assert json.loads(detail["units"][0]["locator"])["page"] == 1
    assert "USD 99" in detail["units"][0]["text"]


@pytest.mark.parametrize("question,start,end", [
    ("last month", "2026-08-31T18:30:00+00:00", "2026-09-30T18:30:00+00:00"),
    ("yesterday", "2026-10-25T18:30:00+00:00", "2026-10-26T18:30:00+00:00"),
    ("today", "2026-10-26T18:30:00+00:00", "2026-10-27T18:30:00+00:00"),
])
def test_relative_time_uses_user_timezone(question, start, end):
    result = window(question, "Asia/Kolkata", now=datetime(2026, 10, 27, 2, tzinfo=timezone.utc))
    assert result["start"] == start and result["end_exclusive"] == end


def test_january_last_month_rolls_back_year():
    result = window("last month", "UTC", now=datetime(2027, 1, 5, tzinfo=timezone.utc))
    assert result["start"].startswith("2026-12-01")
    assert result["end_exclusive"].startswith("2027-01-01")


@pytest.mark.parametrize("segments", [[{"start": float("nan"), "end": 3, "text": "x"}],
                                         [{"start": 0, "end": 3, "text": ""}], [], "wrong"])
def test_bad_transcripts(segments):
    with pytest.raises(ValueError):
        transcript_units(segments)
