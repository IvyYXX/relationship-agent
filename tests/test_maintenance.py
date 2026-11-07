"""Regression boundaries for import-safe operator scripts and shared HTTP policy.

保护维护约定：导入运维脚本不能改配置或启动服务；模型与检索共用
禁止重定向策略；提示词迁移后旧导入路径仍然可用。
"""

from contextlib import ExitStack
from pathlib import Path
import runpy
import sys
import unittest
from unittest.mock import patch
from urllib.request import Request

from relationship_agent.http import NoRedirect
from relationship_agent import prompts, schemas

ROOT = Path(__file__).resolve().parents[1]


class MaintenanceTests(unittest.TestCase):
    """验证模块导入与共享基础设施的维护约定。"""

    def test_operator_scripts_have_no_import_time_side_effects(self):
        # Importing a helper for inspection must not prompt, spend credit, bind ports,
        # mutate configuration, or start a long-running backup process.
        operations = (
            "builtins.input",
            "getpass.getpass",
            "dotenv.load_dotenv",
            "relationship_agent.beta.database.Database",
            "socket.socket",
            "subprocess.Popen",
            "argparse.ArgumentParser.parse_args",
            "pathlib.Path.mkdir",
            "pathlib.Path.write_text",
            "time.sleep",
        )
        hook = sys.excepthook
        with ExitStack() as stack:
            for operation in operations:
                stack.enter_context(
                    patch(operation, side_effect=AssertionError(operation))
                )
            for name in (
                "configure_model",
                "live_smoke",
                "browser_check",
                "backup_loop",
            ):
                with self.subTest(script=name):
                    namespace = runpy.run_path(str(ROOT / "scripts" / (name + ".py")))
                    self.assertTrue(callable(namespace["main"]))
                    self.assertIs(sys.excepthook, hook)

    def test_shared_redirect_policy_never_reissues_credentials(self):
        request = Request(
            "https://provider.invalid/messages",
            headers={"Authorization": "Bearer synthetic"},
        )
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                self.assertIsNone(
                    NoRedirect().redirect_request(
                        request, None, status, "redirect", {}, "https://other.invalid"
                    )
                )

    def test_prompt_import_compatibility_and_stage_coverage(self):
        self.assertIs(schemas.INSTRUCTIONS, prompts.INSTRUCTIONS)
        self.assertEqual(set(schemas.SCHEMAS), set(prompts.INSTRUCTIONS))


if __name__ == "__main__":
    unittest.main()
