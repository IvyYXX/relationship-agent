"""Reproducible isolated browser smoke test; synthetic data only."""

import os
import argparse
import socket
import subprocess
import tempfile
import time
from pathlib import Path
import sys
import traceback
import httpx
from playwright.sync_api import sync_playwright, expect


def report_failure(kind, value, tb):
    """输出合成测试异常，在 CI 中额外生成检查注释。"""
    traceback.print_exception(kind, value, tb)
    if os.getenv("GITHUB_ACTIONS") == "true":
        # Only disposable synthetic fixtures run here; make CI failures visible in check annotations.
        detail = "".join(traceback.format_exception(kind, value, tb))[-10000:]
        print(
            "::error title=Browser regression::"
            + detail.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        )


def main():
    """启动隔离 mock 服务，在真实浏览器验证人物和记忆闭环。"""
    sys.excepthook = report_failure
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"

    with tempfile.TemporaryDirectory() as d:
        env = {
            **os.environ,
            "DATABASE_URL": "sqlite:///" + d + "/browser.sqlite",
            "PERSONA_ENV": "development",
            "PERSONA_MODE": "mock",
            "PERSONA_ORIGIN": origin,
            "PERSONA_INVITE_CODE": "",
        }
        process = subprocess.Popen(
            [sys.executable, "persona_app.py", "--port", str(port)],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(50):
                if process.poll() is not None:
                    raise RuntimeError("Isolated test server exited during startup")
                try:
                    if httpx.get(origin + "/readyz", timeout=1).is_success:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.1)
            # Existing synthetic login avoids interacting with any external account or legal agreement.
            r = httpx.post(
                origin + "/api/auth/register",
                headers={"Origin": origin},
                json={
                    "username": "qa_browser",
                    "password": "synthetic-password-123",
                    "adult": True,
                    "consent": True,
                },
            )
            r.raise_for_status()
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    executable_path=os.getenv("PERSONA_TEST_BROWSER_EXECUTABLE") or None
                )
                page = browser.new_page(viewport={"width": 1360, "height": 880})
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(origin + "/")
                page.locator("#username").fill("qa_browser")
                page.locator("#password").fill("synthetic-password-123")
                page.locator("#auth-submit").click()
                expect(page.locator("#workspace")).to_be_visible()
                page.locator("#add-person").click()
                page.locator("#person-form [name=nickname]").fill("小林")
                page.locator("#person-form button[type=submit]").click()
                expect(page.locator("#person-title")).to_have_text("小林")
                page.locator("#talk-person").click()
                page.locator("#message-input").fill("小林昨天取消约会，我有点失望。")
                page.locator("#message-input").press("Enter")
                page.locator(".memory-card").get_by_role(
                    "button", name="查看并确认", exact=True
                ).click(timeout=10000)
                page.locator("#event-form button[type=submit]").click()
                expect(page.locator(".memory-card")).to_have_count(0)
                page.locator("#new-chat").click()
                page.locator("#message-input").fill("小林之前做过什么？")
                page.locator("#send").click()
                expect(page.locator("#messages")).to_contain_text(
                    "你之前确认记录过", timeout=10000
                )
                page.reload()
                expect(page.locator("#messages")).to_contain_text("你之前确认记录过")
                # Progressive disclosure must keep the chat focused on the requested output.
                expect(page.locator("#message-input")).to_have_count(1)
                trace = (
                    page.locator("#messages details")
                    .filter(has=page.locator("summary", has_text="运行详情"))
                    .first
                )
                assert not trace.evaluate("el => el.open")
                page.locator("#new-chat").click()
                page.locator("#message-input").fill("什么是依恋？")
                page.locator("#message-input").press("Enter")
                sources = page.locator("#messages details.sources")
                expect(sources).to_be_visible(timeout=10000)
                assert not sources.evaluate("el => el.open")
                page.locator("#new-chat").click()
                page.locator("#message-input").fill(
                    "请给我详细分析报告，他一直没有联系我。"
                )
                page.locator("#message-input").press("Enter")
                report = page.locator("#messages details").filter(
                    has=page.locator("summary", has_text="查看详细分析")
                )
                expect(report).to_be_visible(timeout=10000)
                assert not report.evaluate("el => el.open")
                report.locator("summary").click()
                assert report.evaluate("el => el.open")
                page.locator("#new-chat").click()
                page.locator("#message-input").fill("帮我演练一次沟通。")
                page.locator("#message-input").press("Enter")
                rehearsal = page.locator("#messages details").filter(
                    has=page.locator("summary", has_text="沟通演练")
                )
                expect(rehearsal).to_be_visible(timeout=10000)
                assert rehearsal.evaluate("el => el.open")
                page.locator("#language").click()
                expect(page.locator("#new-chat")).to_contain_text("New chat")
                page.screenshot(
                    path=str(args.output_dir / "persona-desktop.png"), full_page=True
                )
                page.set_viewport_size({"width": 390, "height": 844})
                expect(page.locator("#menu-toggle")).to_be_visible()
                page.locator("#menu-toggle").click()
                page.locator("#people-list button").click()
                expect(page.locator("#person-panel")).to_be_visible()
                expect(page.locator("#sidebar")).not_to_have_class("open")
                page.locator("#talk-person").click()
                expect(page.locator("#person-panel")).to_be_hidden()
                assert page.evaluate(
                    "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
                )
                page.screenshot(
                    path=str(args.output_dir / "persona-mobile.png"), full_page=True
                )
                assert not errors, errors
                browser.close()
                print(
                    "Browser flow passed: login, person, Enter, confirmation, cross-session recall, reload, language, mobile."
                )
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == "__main__":
    main()
