# -*- coding: utf-8 -*-
"""ReviewPoint 表：建表、默认值、(task_id, seq) 唯一约束。"""
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from mio_taskhub.db import engine
from mio_taskhub.models import ReviewPoint


def test_review_point_defaults_and_unique():
    with Session(engine) as s:
        p = ReviewPoint(id='RP-t0000001-01', task_id='t0000001', seq=1, claim='可证伪命题')
        s.add(p)
        s.commit()
        got = s.get(ReviewPoint, 'RP-t0000001-01')
        assert got is not None
        assert got.status == 'candidate'
        assert got.risk_level == 'medium'
        assert got.origin == 'rule'
        assert got.annotations == []
        assert got.fr_refs == [] and got.tc_refs == []
        assert got.source_version == ''

        # 同 (task_id, seq) 再插一行 → 唯一约束拒绝
        dup = ReviewPoint(id='RP-t0000001-02', task_id='t0000001', seq=1, claim='重复')
        s.add(dup)
        try:
            s.commit()
            raise AssertionError('(task_id, seq) 唯一约束未生效')
        except IntegrityError:
            s.rollback()
        finally:
            s.delete(got)
            s.commit()
