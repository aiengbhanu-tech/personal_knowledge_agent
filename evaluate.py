"""Small offline regression evaluation, not a production quality benchmark."""
import argparse
import json
from pathlib import Path
import statistics
from tempfile import TemporaryDirectory
from time import perf_counter

from app.config import Settings
from app.db import Database
from app.service import KnowledgeService


def run():
    root = Path(__file__).parent
    examples = root / "examples"
    cases = json.loads((examples / "eval_cases.json").read_text())
    with TemporaryDirectory() as tmp:
        settings = Settings(db_path=Path(tmp)/"eval.sqlite", api_key="evaluation-key-offline-only")
        service = KnowledgeService(Database(settings.db_path), settings)
        for name, date in (("pricing-september.md", "2026-09-18T10:00:00+05:30"),
                           ("pricing-october.md", "2026-10-20T10:00:00+05:30"),
                           ("meeting.json", None), ("pilot.eml", None),
                           ("security.html", "2026-10-21T10:00:00+05:30")):
            service.ingest((examples/name).read_bytes(), name, date, None, False)
        results = []
        latencies = []
        citations_checked = 0
        for case in cases:
            from datetime import date
            kwargs = {k: date.fromisoformat(v) if k.endswith("_date") else v
                      for k, v in case.items() if k in ("start_date", "end_date", "modality")}
            started = perf_counter()
            result = service.ask(case["question"], **kwargs)
            elapsed = (perf_counter()-started)*1000
            latencies.append(elapsed)
            expected_abstain = case.get("expect_abstain", False)
            passed = result["abstained"] if expected_abstain else any(
                e["filename"] == case["expected_file"] and case["expected_quote"] in e["quote"]
                for e in result["evidence"])
            with service.db.connect() as c:
                for e in result["evidence"]:
                    unit = c.execute("SELECT text FROM units WHERE id=?", (e["unit_id"],)).fetchone()[0]
                    assert unit[e["start_char"]:e["end_char"]] == e["quote"]
                    citations_checked += 1
            results.append({"question": case["question"], "passed": bool(passed),
                            "expected_abstention": expected_abstain, "latency_ms": round(elapsed, 3)})
        positive = [r for r in results if not r["expected_abstention"]]
        negative = [r for r in results if r["expected_abstention"]]
        return {
            "scope": "16 hand-authored synthetic regression cases; small local corpus; no held-out generalization claim",
            "retrieval_mode": "offline FTS5; no cloud calls",
            "cases_passed": sum(r["passed"] for r in results), "cases_total": len(results),
            "source_and_quote_hit_at_5": sum(r["passed"] for r in positive)/len(positive),
            "abstention_accuracy": sum(r["passed"] for r in negative)/len(negative),
            "exact_citations_verified": citations_checked,
            "retrieval_latency_median_ms": round(statistics.median(latencies), 3),
            "retrieval_latency_p95_ms": round(sorted(latencies)[int(.95 * (len(latencies)-1))], 3),
            "latency_method": "16 sequential in-process requests; includes SQLite reads, excludes HTTP/ingestion/cloud; nearest lower percentile",
            "results": results,
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run()
    rendered = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n")
    print(rendered)
    raise SystemExit(0 if report["cases_passed"] == report["cases_total"] else 1)
