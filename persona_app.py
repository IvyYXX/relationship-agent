"""Run Persona AI. web_app.py is an equivalent entry point."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent / "src"))
from relationship_agent.beta.app import main

if __name__ == "__main__":
    main()
