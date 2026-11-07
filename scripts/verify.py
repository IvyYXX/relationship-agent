"""Run repeatable local acceptance checks on synthetic, temporary databases only.

默认依次运行静态检查、格式、回归与两套 Mock 评测；每项保存独立日志。
--browser 和 --package 才增加较重的环境检查。非零退出码用于阻止 CI 放行，
summary 不把工程测试写成模型质量或公网部署验收。
"""

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
    # 强制使用 Mock 与测试自建的临时库；继承环境只为找到必要运行工具。
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
        # 各检查独立留日志，前一项失败仍继续收集后续问题，最终统一非零退出。
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
