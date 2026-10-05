"""配置端点（想法落地闭环 P2，FR-19/FR-20）。

- GET  /api/v1/config/role-prompts  读角色 prompt + 高风险词表（含来源）
- PUT  /api/v1/config/role-prompts  批量改 prompt（有变化 version+1，缓存失效=热更新）
- PUT  /api/v1/config/risk-vocab    改高风险词表（写 DB；读取优先级 env > DB > 默认）
"""
import os

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from mio_taskhub.db import get_session
from mio_taskhub import role_prompts as rp
from mio_taskhub.next_action import risk_tag_vocab

router = APIRouter(prefix="/config", tags=["config"])


def _vocab_source(db: Session) -> str:
    if os.environ.get("MIO_IDEA_RISK_TAGS", "").strip():
        return "env"
    return "db" if rp.vocab_configured(db) else "default"


@router.get("/role-prompts")
def get_role_prompts(db: Session = Depends(get_session)):
    prompts = rp.load_prompts(db)
    return {
        "prompts": prompts,
        "roles": list(prompts.keys()),
        "risk_vocab": risk_tag_vocab(db),
        "risk_vocab_source": _vocab_source(db),
    }


@router.put("/role-prompts")
def put_role_prompts(body: dict, db: Session = Depends(get_session)):
    updates = body.get("prompts")
    if not isinstance(updates, dict) or not updates:
        raise HTTPException(422, "prompts must be a non-empty object {role: text}")
    try:
        prompts = rp.save_prompts(db, updates)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"prompts": prompts, "roles": list(prompts.keys()),
            "risk_vocab": risk_tag_vocab(db), "risk_vocab_source": _vocab_source(db)}


@router.put("/risk-vocab")
def put_risk_vocab(body: dict, db: Session = Depends(get_session)):
    words = body.get("words")
    try:
        cleaned = rp.save_risk_vocab(db, words)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"words": cleaned, "source": _vocab_source(db)}
