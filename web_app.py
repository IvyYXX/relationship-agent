#!/usr/bin/env python3
"""Run this file in PyCharm, then open the printed local URL."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from relationship_agent.beta.app import main

if __name__ == "__main__":
    main()
