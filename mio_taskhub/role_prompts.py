"""Agent 角色 prompt + 高风险词表：数据库存储 + 进程内缓存（想法落地闭环 P2，FR-19/FR-20）。

- 存储：RolePrompt 表（role/prompt/version 递增）+ AppConfig 表（risk_vocab 词表）；
- 缓存：启动后首次读取加载，配置端点写入后失效（热更新）；
- 快照：评审会创建时按 roles 一次性读取 prompt+version 落 Discussion.prompt_snapshot，
  进行中会话只读快照，不受后续热更新影响（FR-19/NFR-2）。
"""
from sqlmodel import Session, select

from mio_taskhub.models import AppConfig, RolePrompt
from mio_taskhub.utils import _now

# 种子角色（设计稿「角色能力约定」表）
DEFAULT_ROLE_PROMPTS = {
    "产品": "以产品视角评审：关注需求真伪、用户价值与 MVP 取舍，警惕范围蔓延。",
    "技术": "以技术视角评审：关注可行性、实现成本、依赖与技术债，指出隐藏的工程约束。",
    "商业": "以商业视角评审：关注获客、成本、收益与竞争差异，验证商业假设是否成立。",
    "合规": "以合规视角评审：关注隐私、法务与政策风险，任何用户数据/资金流转必须点名审查。",
    "红队": "以红队视角评审：关注反用法、失败模式与最坏情况，主动攻击方案弱点，给出可复现的破坏路径。",
}
DEFAULT_RISK_VOCAB = ["高风险", "合规", "用户数据", "花钱"]

# 进程内缓存：None=未加载；dict=已加载（配置端点写入后置 None 触发热更新）
_PROMPT_CACHE: dict | None = None
_VOCAB_CACHE: list | None = None


def _cache_key(row: RolePrompt) -> tuple:
    return (row.role, row.prompt, row.version)


def ensure_seeds(db: Session) -> None:
    """空表时写入 5 个默认角色（幂等，不覆盖已有数据）。

    注意：不预埋 risk_vocab 词表行——「未配置」与「默认值」需可区分（FR-20 来源语义）。
    """
    existing = db.exec(select(RolePrompt)).all()
    if existing:
        return
    now = _now()
    for role, prompt in DEFAULT_ROLE_PROMPTS.items():
        db.add(RolePrompt(role=role, prompt=prompt, version=1, updated_at=now))
    db.commit()


def load_prompts(db: Session) -> dict:
    """role -> {prompt, version, updated_at}；带缓存，调用 ensure_seeds 保证有种子。"""
    global _PROMPT_CACHE
    if _PROMPT_CACHE is not None:
        return _PROMPT_CACHE
    ensure_seeds(db)
    rows = db.exec(select(RolePrompt)).all()
    _PROMPT_CACHE = {r.role: {"prompt": r.prompt, "version": r.version,
                              "updated_at": r.updated_at.isoformat()} for r in rows}
    return _PROMPT_CACHE


def save_prompts(db: Session, updates: dict) -> dict:
    """批量更新 prompt（有变化才 version+1），写库后失效缓存。返回更新后的全量。"""
    global _PROMPT_CACHE
    ensure_seeds(db)
    now = _now()
    for role, prompt in updates.items():
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError(f"prompt for role '{role}' must be a non-empty string")
        row = db.exec(select(RolePrompt).where(RolePrompt.role == role)).first()
        if row is None:
            db.add(RolePrompt(role=role, prompt=prompt.strip(), version=1, updated_at=now))
        elif row.prompt != prompt.strip():
            row.prompt = prompt.strip()
            row.version = (row.version or 1) + 1
            row.updated_at = now
            db.add(row)
    db.commit()
    _PROMPT_CACHE = None
    return load_prompts(db)


def snapshot_for(db: Session, roles: list) -> dict:
    """评审创建时一次性读取 roles 对应 prompt+version（NFR-2：单次读取无半新半旧）。"""
    prompts = load_prompts(db)
    return {
        "roles": list(roles),
        "prompts": {r: {"version": (prompts.get(r) or {}).get("version"),
                        "prompt": (prompts.get(r) or {}).get("prompt")}
                    for r in roles},
        "captured_at": _now().isoformat(),
    }


def load_risk_vocab_db(db: Session) -> list:
    """读 DB 词表（带缓存）：未配置返回 []，由上层回落默认常量。"""
    global _VOCAB_CACHE
    if _VOCAB_CACHE is not None:
        return _VOCAB_CACHE
    ensure_seeds(db)
    row = db.exec(select(AppConfig).where(AppConfig.key == "risk_vocab")).first()
    _VOCAB_CACHE = list(row.value) if row is not None and isinstance(row.value, list) else []
    return _VOCAB_CACHE


def vocab_configured(db: Session) -> bool:
    """DB 是否已配置词表行（GET 配置端点报来源用；读取路径走缓存不查它）。"""
    row = db.exec(select(AppConfig).where(AppConfig.key == "risk_vocab")).first()
    return row is not None


def save_risk_vocab(db: Session, words: list) -> list:
    """写入 DB 词表并失效缓存（env 覆盖时仍可写，读取优先级 env > DB > 默认）。"""
    global _VOCAB_CACHE
    if not isinstance(words, list) or not all(isinstance(w, str) and w.strip() for w in words):
        raise ValueError("words must be a list of non-empty strings")
    cleaned = [w.strip() for w in words]
    ensure_seeds(db)
    row = db.exec(select(AppConfig).where(AppConfig.key == "risk_vocab")).first()
    if row is None:
        row = AppConfig(key="risk_vocab", value=cleaned, updated_at=_now())
    else:
        row.value = cleaned
        row.updated_at = _now()
    db.add(row)
    db.commit()
    _VOCAB_CACHE = cleaned
    return cleaned


def invalidate() -> None:
    """全局失效（测试隔离 / 配置联动用）。"""
    global _PROMPT_CACHE, _VOCAB_CACHE
    _PROMPT_CACHE = None
    _VOCAB_CACHE = None
