"""Daily encrypted snapshots; run with persistent backup storage, not as an HTTP service."""

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from relationship_agent.beta.database import Database
from relationship_agent.beta.ops import backup


def main():
    """按日创建加密备份并清理七天前的备份；仅用于专用运维进程。"""
    db = Database(os.environ["DATABASE_URL"])
    key = os.environ["PERSONA_BACKUP_KEY"]
    root = Path("/backups")
    while True:
        filename = root / (
            "persona-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + ".enc"
        )
        backup(db, filename, key)
        for old in root.glob("persona-*.enc"):
            if old.stat().st_mtime < time.time() - 7 * 86400:
                old.unlink()
        print("Encrypted backup complete; retention enforced.", flush=True)
        time.sleep(86400)


if __name__ == "__main__":
    main()
