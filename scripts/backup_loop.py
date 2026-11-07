"""Daily encrypted snapshots; run with persistent backup storage, not as an HTTP service.

供 compose 的可选 backup 服务运行。每轮先成功生成加密快照，再清理
同目录七天前的快照；数据库地址和备份密钥由部署环境注入。
"""

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
        # 新快照失败会直接抛错，只有成功后才清理旧快照，避免先删除可用备份。
        for old in root.glob("persona-*.enc"):
            if old.stat().st_mtime < time.time() - 7 * 86400:
                old.unlink()
        print("Encrypted backup complete; retention enforced.", flush=True)
        time.sleep(86400)


if __name__ == "__main__":
    main()
