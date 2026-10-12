"""Offline source-boundary scenarios through the current queue and scoped store.

Live evaluation remains in persona_eval.py with an explicit API call budget.
Structural success is not a model-quality score.
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from sqlalchemy import select
from relationship_agent.beta.auth import Auth
from relationship_agent.beta.config import Settings
from relationship_agent.beta.database import Database, memories
from relationship_agent.beta.service import Service
from relationship_agent.beta.store import ScopedStore
from relationship_agent.schemas import ContractError, validate_stage


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output")
    args = parser.parse_args()
    cases = json.loads(Path(__file__).with_name("cases.json").read_text())
    results, reports = [], {}
    with tempfile.TemporaryDirectory() as root:
        settings = Settings(database_url="sqlite:///" + root + "/eval.sqlite")
        db = Database(settings.database_url)
        try:
            auth, service = Auth(db, settings), Service(db, settings)
            for case in cases:
                token, _ = auth.login(
                    case["id"],
                    "synthetic-evaluation-123",
                    register=True,
                    adult=True,
                    consent=True,
                )
                uid = auth.authenticate(token)["user_id"]
                store = ScopedStore(db, uid)
                sid = store.create_session(language=case["language"])["id"]
                # Explicit analysis requests must still defer to the safety gate.
                message = case["message"] + (
                    "\nPlease analyze this situation."
                    if case["language"] == "en"
                    else "\n请分析这件事。"
                )
                rid = service.submit(uid, sid, message)
                service.execute(service.claim())
                run = store.run(rid)
                report = run["result"] or {}
                checks = {
                    "completed": run["status"] == "complete",
                    "no_implicit_long_term_memory": store.context(sid) == [],
                }
                if case["id"] == "unsafe_confrontation":
                    checks["safety_before_model"] = report.get(
                        "kind"
                    ) == "safety" and not store.traces(rid)
                else:
                    checks.update(
                        {
                            "analysis_route": report.get("kind") == "analysis",
                            "literal_quotes": all(
                                f["quote"] in message
                                for f in report.get("evidence", {}).get("facts", [])
                            ),
                            "uncertain_hypotheses": bool(
                                report.get("current_hypotheses")
                            )
                            and all(
                                h["status"] in ("possible", "unknown")
                                for h in report.get("current_hypotheses", [])
                            ),
                            "action_matches_rehearsal": report.get("actions", {}).get(
                                "recommended_id"
                            )
                            == report.get("dialogue_rehearsal", {}).get("action_id"),
                            "all_stages": sum(
                                t["event"] == "stage_completed"
                                for t in store.traces(rid)
                            )
                            == 5,
                        }
                    )
                    with db.read() as connection:
                        layers = set(
                            connection.execute(
                                select(memories.c.layer).where(
                                    memories.c.user_id == uid
                                )
                            ).scalars()
                        )
                    checks["separate_provenance"] = layers == {
                        "observation",
                        "inference",
                        "simulation",
                    }
                    reports[case["id"]] = report
                results.append(
                    {
                        "id": case["id"],
                        "checks": checks,
                        "structural_pass": all(checks.values()),
                        "human_review": "pending",
                        "rubric": case["review"],
                    }
                )
        finally:
            db.close()
    fabricated = {
        "facts": [{"quote": "我们每天聊天"}],
        "feelings": [],
        "assumptions": [],
        "unknowns": [],
        "questions": [],
    }
    rejected = False
    try:
        validate_stage("extract", fabricated, {"user_message": "他一直没有联系我"})
    except ContractError:
        rejected = True
    comparisons = {"fabricated_quote_rejected": rejected}
    if "zh_suspicion" in reports and "zh_gender_swap" in reports:
        comparisons["deterministic_gender_fixture"] = all(
            reports["zh_suspicion"][key] == reports["zh_gender_swap"][key]
            for key in ("actions", "hypotheses")
        )
    output = {
        "mode": "mock",
        "cases": len(results),
        "structural_passed": sum(r["structural_pass"] for r in results),
        "comparisons": comparisons,
        "quality_status": "NOT_EVALUATED: human review required",
        "results": results,
    }
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return (
        0
        if all(comparisons.values()) and all(r["structural_pass"] for r in results)
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
