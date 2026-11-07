"""Local hidden key entry and safe .env update; never prints the secret.

只在 main 中交互，输入密钥不回显。保留 .env 里的其他配置，
用 JSON 引号编码值以兼容特殊字符；修改后需要重启服务才会重新读取。
"""

from pathlib import Path
import getpass
import json
import os
import re


def main():
    """交互式配置模型并以 0600 权限写入 .env，不调用模型。"""
    root = Path(__file__).resolve().parents[1]
    p = root / ".env"
    text = p.read_text() if p.exists() else (root / ".env.example").read_text()
    print("Persona AI · configure a model you are authorized to use")
    provider = input("Provider [glm/openai/custom; default glm]: ").strip() or "glm"
    if provider not in ("glm", "openai", "custom"):
        raise SystemExit("Unknown provider")
    url = (
        input("Base URL (blank uses provider default): ").strip()
        or {
            "glm": "https://open.bigmodel.cn/api/paas/v4",
            "openai": "https://api.openai.com/v1",
            "custom": "",
        }[provider]
    )
    model = input("Model ID: ").strip()
    if not model or not url:
        raise SystemExit("Model and URL required")
    key = getpass.getpass("API key (hidden; stored only in ignored .env): ").strip()
    if not key:
        raise SystemExit("No key entered; no changes made")
    values = {
        "PERSONA_MODE": "api",
        "PERSONA_API_KEY": key,
        "PERSONA_PROVIDER_LABEL": provider,
        "RELATIONSHIP_MODEL": model,
        "RELATIONSHIP_BASE_URL": url,
        "RELATIONSHIP_PROTOCOL": "chat-completions",
    }
    for name, value in values.items():
        # JSON 引号保护空格等字符；替换回调用字面值，避免正则解释值中的反斜杠。
        line = name + "=" + json.dumps(value)
        text = (
            re.sub(r"^" + name + r"=.*$", lambda _: line, text, flags=re.M)
            if re.search(r"^" + name + r"=", text, re.M)
            else text + "\n" + line + "\n"
        )
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    # 文件已存在时 open 的 mode 不会重设权限，所以还需显式限制为当前用户可读写。
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    print(
        "Saved .env with mode 0600. Restart persona_app.py. No model call has been made."
    )


if __name__ == "__main__":
    main()
