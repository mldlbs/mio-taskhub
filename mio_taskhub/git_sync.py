"""Git Sync Worker: 异步消费 OutboxEvent，将 ADR 投影到 Git 仓库。

职责边界：
- Git 是派生物（projection），不是 Source of Truth
- 一个领域事件 = 一个 Git commit
- 幂等：重启/重复消费不产生错误状态
- 只处理 ADR 相关事件，不扩展到所有 Idea 事件
"""
import os
import subprocess
import logging
import threading
import time
from pathlib import Path
from datetime import datetime
from typing import Optional

from sqlmodel import Session, select

from .db import engine
from .models import OutboxEvent, OutboxStatus, Idea, IdeaType

logger = logging.getLogger("git_sync")

# 配置：MIO_TASKHUB_ADR_DIR 可覆盖默认落盘目录（默认 CWD 下 docs/adr）
POLL_INTERVAL = 2  # 秒
MAX_RETRIES = 3


def _adr_dir() -> Path:
    env = os.environ.get("MIO_TASKHUB_ADR_DIR", "").strip()
    return Path(env) if env else Path("docs/adr")


def _run_git(*args: str) -> bool:
    """执行 git 命令，返回是否成功"""
    try:
        result = subprocess.run(
            ["git"] + list(args),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.error(f"git {' '.join(args)} failed: {result.stderr}")
            return False
        return True
    except Exception as e:
        logger.error(f"git {' '.join(args)} exception: {e}")
        return False


def _ensure_adr_dir():
    """确保 ADR 目录存在"""
    _adr_dir().mkdir(parents=True, exist_ok=True)


def _render_adr_markdown(idea: Idea) -> str:
    """将 ADR 数据渲染为 MADR 格式 Markdown"""
    status_str = idea.adr_status.value.upper() if idea.adr_status else "PROPOSED"
    adr_num = f"ADR-{idea.adr_number:03d}" if idea.adr_number else f"ADR-{idea.id[:8]}"

    lines = [
        f"# {adr_num}: {idea.title}",
        "",
        "## Status",
        "",
        status_str,
        "",
    ]

    if idea.superseded_by:
        lines += ["## Superseded By", "", idea.superseded_by, ""]

    lines += ["## Context", "", idea.madr_context or "(to be completed)", ""]
    lines += ["## Decision", "", idea.madr_decision or "(to be completed)", ""]

    # 备选方案（兼容字符串 / 列表两种输入）
    if idea.madr_alternatives:
        lines += ["## Alternatives", ""]
        alts = idea.madr_alternatives
        if isinstance(alts, str):
            lines.append(alts)
        else:
            for idx, alt in enumerate(alts, 1):
                if isinstance(alt, dict):
                    lines.append(f"{idx}. **{alt.get('title', '')}**: {alt.get('description', '')}")
                else:
                    lines.append(f"{idx}. {alt}")
        lines += [""]

    lines += ["## Consequences", "", idea.madr_consequences or "(to be completed)", ""]
    lines += [
        "## Metadata",
        "",
        f"- Idea ID: `{idea.id}`",
        f"- Version: {idea.version}",
        f"- Created: {idea.created_at.isoformat() if idea.created_at else 'N/A'}",
        f"- Updated: {idea.updated_at.isoformat() if idea.updated_at else 'N/A'}",
    ]

    return "\n".join(lines) + "\n"


def _render_readme(adrs: list[Idea]) -> str:
    """生成 ADR 索引 README"""
    lines = [
        "# ADR Index",
        "",
        "| Number | Title | Status | Updated |",
        "|--------|-------|--------|---------|",
    ]
    for adr in adrs:
        num = f"ADR-{adr.adr_number:03d}" if adr.adr_number else f"ADR-{adr.id[:8]}"
        status = adr.adr_status.value if adr.adr_status else "unknown"
        updated = adr.updated_at.strftime("%Y-%m-%d") if adr.updated_at else "N/A"
        lines.append(f"| {num} | {adr.title} | {status} | {updated} |")

    return "\n".join(lines) + "\n"


def _get_adr_filename(idea: Idea) -> str:
    """获取 ADR 文件名"""
    if idea.adr_number:
        return f"ADR-{idea.adr_number:03d}.md"
    return f"ADR-{idea.id[:8]}.md"


def _process_event(db: Session, event: OutboxEvent) -> bool:
    """处理单个 OutboxEvent，返回是否成功"""
    idea = db.get(Idea, event.aggregate_id)
    if not idea:
        logger.error(f"Event {event.id}: idea {event.aggregate_id} not found")
        return False

    _ensure_adr_dir()

    if event.event_type == "evolve-to-adr":
        # 渲染 ADR 文件
        content = _render_adr_markdown(idea)
        filename = _get_adr_filename(idea)
        filepath = _adr_dir() / filename
        filepath.write_text(content, encoding="utf-8")

        # 更新 idea 的文件路径
        idea.adr_file_path = str(filepath)
        db.add(idea)

    elif event.event_type in ("accept", "reject", "deprecate", "supersede"):
        # 重新渲染 ADR 文件（状态已更新）
        filename = _get_adr_filename(idea)
        filepath = _adr_dir() / filename
        if filepath.exists():
            content = _render_adr_markdown(idea)
            filepath.write_text(content, encoding="utf-8")
        else:
            logger.warning(f"ADR file not found: {filepath}, rendering new")
            content = _render_adr_markdown(idea)
            filepath.write_text(content, encoding="utf-8")
            idea.adr_file_path = str(filepath)
            db.add(idea)
    else:
        logger.warning(f"Unknown event type: {event.event_type}")
        return False

    # 生成 README
    all_adrs = db.exec(
        select(Idea)
        .where(Idea.idea_type == IdeaType.ADR)
        .where(Idea.adr_number.is_not(None))
        .order_by(Idea.adr_number)
    ).all()
    readme_content = _render_readme(all_adrs)
    (_adr_dir() / "README.md").write_text(readme_content, encoding="utf-8")

    # Git commit: 一个事件 = 一个 commit
    adr_file = str(filepath)
    readme_file = str(_adr_dir() / "README.md")
    _run_git("add", adr_file, readme_file)

    commit_msg = f"ADR-{idea.adr_number:03d}: {event.event_type}" if idea.adr_number else f"ADR: {event.event_type}"
    if event.event_type == "supersede" and event.payload.get("superseded_by_number"):
        replacement_num = event.payload["superseded_by_number"]
        commit_msg += f" by ADR-{replacement_num:03d}"

    if not _run_git("commit", "-m", commit_msg):
        return False

    logger.info(f"Synced event {event.id}: {commit_msg}")
    return True


def sync_pending_events():
    """同步所有 pending 事件（阻塞式轮询）"""
    with Session(engine) as db:
        pending = db.exec(
            select(OutboxEvent)
            .where(OutboxEvent.status == OutboxStatus.PENDING)
            .order_by(OutboxEvent.id)
        ).all()

        for event in pending:
            # 标记为 processing
            event.status = OutboxStatus.PROCESSING
            db.add(event)
            db.commit()

            try:
                success = _process_event(db, event)
                if success:
                    event.status = OutboxStatus.SYNCED
                    event.processed_at = datetime.now()
                else:
                    event.retry_count += 1
                    if event.retry_count >= event.max_retries:
                        event.status = OutboxStatus.FAILED
                        event.error = "max retries exceeded"
                    else:
                        event.status = OutboxStatus.PENDING  # 重试
            except Exception as e:
                event.retry_count += 1
                event.error = str(e)[:500]
                if event.retry_count >= event.max_retries:
                    event.status = OutboxStatus.FAILED
                else:
                    event.status = OutboxStatus.PENDING

            db.add(event)
            db.commit()


class GitSyncWorker:
    """异步 Git Sync Worker，独立线程运行"""

    def __init__(self, poll_interval: float = POLL_INTERVAL):
        self.poll_interval = poll_interval
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        """启动 Worker"""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("Git Sync Worker started")

    def stop(self):
        """停止 Worker"""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Git Sync Worker stopped")

    def _run(self):
        while not self._stop_event.is_set():
            try:
                sync_pending_events()
            except Exception as e:
                logger.error(f"Git Sync Worker error: {e}")
            self._stop_event.wait(self.poll_interval)


# 全局 Worker 实例
_worker: Optional[GitSyncWorker] = None


def start_git_sync_worker():
    """启动全局 Git Sync Worker"""
    global _worker
    if _worker and _worker._thread and _worker._thread.is_alive():
        return
    _worker = GitSyncWorker()
    _worker.start()


def stop_git_sync_worker():
    """停止全局 Git Sync Worker"""
    global _worker
    if _worker:
        _worker.stop()
        _worker = None
