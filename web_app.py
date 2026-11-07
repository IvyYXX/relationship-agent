#!/usr/bin/env python3
"""Run this file in PyCharm, then open the printed local URL.

适合直接在 IDE 中运行。把本仓库 src 放入导入路径后调用统一入口，
无需安装另一个同名服务；监听端口和公开来源需要在配置中保持一致。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from relationship_agent.beta.app import main

if __name__ == "__main__":
    main()
