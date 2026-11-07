"""Run Persona AI. web_app.py is an equivalent entry point.

与 web_app.py 等价，保留两个名字兼容已有启动方式。应用配置、鉴权和
模型选择都在包内处理，避免在启动文件各复制一份。
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent / "src"))
from relationship_agent.beta.app import main

if __name__ == "__main__":
    main()
