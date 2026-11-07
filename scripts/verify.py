"""Run repeatable local acceptance checks on synthetic, temporary databases only."""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    """运行离线验收并保存逐项日志；仅显式选项启动浏览器或构建。"""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs")
        / ("verification-" + datetime.now().strftime("%Y%m%d-%H%M%S")),
    )
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--package", action="store_true")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PERSONA_MODE": "mock",
        "PERSONA_ENV": "development",
    }
    # Never let this convenience runner clear a caller's configured Postgres database.
    env.pop("PERSONA_TEST_DATABASE_URL", None)
    checks = [
        ("lint", ["-m", "ruff", "check", "."]),
        ("format", ["-m", "ruff", "format", "--check", "."]),
        (
            "regression",
            ["-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"],
        ),
        ("harness", ["evals/run_eval.py", "--output", str(output / "harness.json")]),
        (
            "persona",
            ["evals/persona_eval.py", "--output", str(output / "persona.json")],
        ),
    ]
    if args.browser:
        checks.append(
            (
                "browser",
                ["scripts/browser_check.py", "--output-dir", str(output / "browser")],
            )
        )
    if args.package:
        checks.append(
            (
                "package",
                ["scripts/build_release.py", "--output-dir", str(output / "dist")],
            )
        )
    results = []
    for name, command in checks:
        print(f"Running {name}...", flush=True)
        started = time.monotonic()
        try:
            result = subprocess.run(
                [sys.executable, *command],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=300,
            )
            code, log = result.returncode, result.stdout
        except subprocess.TimeoutExpired:
            code, log = 124, "Verification exceeded the five-minute check budget."
        (output / (name + ".log")).write_text(log)
        results.append(
            {
                "check": name,
                "returncode": code,
                "seconds": round(time.monotonic() - started, 3),
            }
        )
        print(f"{name}: {'PASS' if code == 0 else 'FAIL'}", flush=True)
    summary = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "results": results,
        "live_model_quality": "NOT_EVALUATED",
        "production_deployment": "NOT_EVALUATED",
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"Logs: {output}")
    return int(any(result["returncode"] != 0 for result in results))


if __name__ == "__main__":
    raise SystemExit(main())
