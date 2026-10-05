"""一次性迁移脚本：将现有 NEW + auto-generated 想法迁移到 INBOX。

运行方式：
    python scripts/migrate_ideas_inbox.py

迁移逻辑：
1. 所有 status=NEW 且 labels 含 "auto-generated" 的想法 -> INBOX
2. 非 auto-generated 的 NEW 保持 NEW（人工创建的想法不受影响）
3. 为 Idea 表新增 novelty/feasibility/impact 列（nullable，兼容旧数据）
4. 记录迁移日志到 taskhub 的 history_event（可选）
"""
import sqlite3
import sys
from datetime import datetime

DB_PATH = r"C:\Users\admin\.mio_taskhub\taskhub.db"


def migrate():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 1. 检查 novelty/feasibility/impact 列是否存在，不存在则添加
    cursor.execute("PRAGMA table_info(idea)")
    columns = [row[1] for row in cursor.fetchall()]
    
    for col in ("novelty", "feasibility", "impact"):
        if col not in columns:
            cursor.execute(f"ALTER TABLE idea ADD COLUMN {col} INTEGER")
            print(f"Added column: {col}")
        else:
            print(f"Column {col} already exists")

    # 2. 查询待迁移的想法
    cursor.execute(
        "SELECT id, title, status, labels FROM idea "
        "WHERE status = 'NEW' AND labels LIKE '%auto-generated%'"
    )
    to_migrate = cursor.fetchall()
    print(f"Found {len(to_migrate)} ideas to migrate from NEW -> INBOX")

    for idea_id, title, status, labels in to_migrate:
        print(f"  Migrating {idea_id}: {title[:50]}")
        cursor.execute(
            "UPDATE idea SET status = 'INBOX', updated_at = ? WHERE id = ?",
            (datetime.now().isoformat(), idea_id)
        )

    # 3. 提交
    conn.commit()
    print("Migration committed.")

    # 4. 验证
    cursor.execute(
        "SELECT COUNT(*) FROM idea WHERE status = 'INBOX'"
    )
    inbox_count = cursor.fetchone()[0]
    print(f"Total INBOX ideas after migration: {inbox_count}")

    cursor.execute(
        "SELECT COUNT(*) FROM idea WHERE status = 'NEW' AND labels LIKE '%auto-generated%'"
    )
    remaining_new = cursor.fetchone()[0]
    print(f"Remaining NEW + auto-generated: {remaining_new}")

    conn.close()
    print("Migration completed successfully.")


if __name__ == "__main__":
    migrate()