# -*- coding: utf-8 -*-
"""T/C 档位定义（env 表，唯一真源）——04 §2.1/§5.3（预注册，冻结后不得改）。

T 档 = 治理默认开（不设 MIO_READ_GATE / MIO_RATCHET_DISABLED）；
C 档 = MIO_READ_GATE=0 + MIO_RATCHET_DISABLED=1。
质量门/生命周期门无 env 开关，C 档用请求体 force=true（已知混杂，04 §8-2）。
"""
from __future__ import annotations

# 观测/调度噪声控制（两档相同，04 §5.3）
NOISE_ENV = {
    "MIO_OBSERVER_AUTOSTART": "0",
    "MIO_DIGEST_DISABLED": "1",
    "MIO_CONTRACT_DISABLED": "1",
    "MIO_TASKHUB_RATE_LIMIT": "100000",
    "MIO_TASKHUB_TOKEN": "",
}

ARM_ENV = {
    "T": dict(NOISE_ENV),
    "C": {**NOISE_ENV, "MIO_READ_GATE": "0", "MIO_RATCHET_DISABLED": "1"},
}

# S4 真服务时长参数（两档同值；T_limit = 2×(5+10)=30s，04 §5.3）
S4_ENV = {
    "MIO_TASKHUB_TIMEOUT_SECONDS": "5",
    "MIO_TASKHUB_AGENT_OFFLINE_SECONDS": "3",
}
T_LIMIT_SECONDS = 30

# 档位探针期望（04 §2.3）：不满足 → 整轮无效
PROBE_EXPECT = {
    "unread_submit": {"T": 422, "C": 200},
    "ratchet_rollback": {"T": 422, "C": 200},
}

# 每档样本量（04 §5.1 预注册 n）
SCENARIO_N = {
    "S1": 30,
    "S2": 30,
    "S2b": 30,
    "S3": 30,
    "S3b": 30,
    "S4a": 42,  # 21×2 亚型
    "S4b": 5,   # 需 §9 P3 批准
    "S5": 30,
    "S6": 30,   # ①15+②15
    "S7": 30,   # move/advance 各 15
}

MASTER_SEED_DEFAULT = "20261010"
ROUNDS_DEFAULT = 3
REQUIRED_KINDS = ("spec", "api", "requirement")

ARM_KEYS = ("T", "C")


def arm_env(arm: str) -> dict:
    if arm not in ARM_ENV:
        raise ValueError(f"unknown arm: {arm}")
    return dict(ARM_ENV[arm])


def full_child_env(base_env: dict, arm: str, *, s4: bool = False) -> dict:
    """子进程 env 表：base + 档位 + 噪声（+S4 时长）。先清两开关再按档设置。"""
    env = dict(base_env)
    env.pop("MIO_READ_GATE", None)
    env.pop("MIO_RATCHET_DISABLED", None)
    env.update(ARM_ENV[arm])
    if s4:
        env.update(S4_ENV)
    return env
