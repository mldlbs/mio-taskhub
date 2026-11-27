# -*- coding: utf-8 -*-
"""H5 真实文档盲回放：用正式模块复现最终口径 22/49 = 44%。

样本来自 csps-app-fxyj/srm-web-fcs/docs/taskhub（历史已评审任务）。
目录不存在时跳过（CI 环境不携带业务仓），本机跑必过。
"""
import os

import pytest

from mio_taskhub.review.extract import extract_review_points
from mio_taskhub.review.h5 import replay, FINAL_WEAK

ROOT = 'E:/work/code/csps-app-fxyj/srm-web-fcs/docs/taskhub'
FILES = [
    'requirement-12-tunnel-waterlogging-warning.md',
    'requirement-19-pipe-health.md',
    '976be80e/plan.md',
    '976be80e/api.md',
]
pytestmark = pytest.mark.skipif(
    not all(os.path.isfile(os.path.join(ROOT, f)) for f in FILES),
    reason='业务仓文档目录不在本机，跳过真实回放')


def _sample(name, rel, scope, golden):
    return dict(name=name, src=os.path.join(ROOT, rel), scope=scope, golden=golden)


SAMPLES = [
    _sample('N1 四级阈值', FILES[0], None, [
        ('τ倍数', [r'τ', r'tau', r'倍数', r'采样周期']),
        ('净高分档', [r'净高分档', r'< 3\s*m', r'净高.*档']),
        ('大小型车', [r'大型车', r'小型车']),
        ('净高不进主键', [r'threshold_cfg', r'主键.*tunnel|tunnel_id.*主键']),
        ('mode=observe', [r'mode=observe', r'\bobserve\b']),
        ('产出契约段', [r'contract_missing', r'产出契约']),
        ('C-1换算', [r'(?<![A-Za-z])C-1\b', r'液位.水深换算']),
        ('C-3限高', [r'(?<![A-Za-z])C-3\b', r'标志牌.*限高']),
        ('C-5净高缺失', [r'(?<![A-Za-z])C-5\b', r'净高缺失']),
        ('昼夜', [r'昼夜', r'夜间']),
        ('回差', [r'回差', r'T_out']),
    ]),
    _sample('需求19 管段健康度', FILES[1], None, [
        ('MI/RI分级', [r'MI/RI', r'MI.*RI']),
        ('四级色带', [r'色带', r'深绿', r'深红']),
        ('分页兼容', [r'records|rows|content', r'分页']),
        ('管段ID归一化', [r'归一化.*管段|管段.*归一化']),
        ('mock兜底', [r'mock.*兜底|兜底.*mock', r'空数据.*兜底']),
        ('诊断报告', [r'诊断报告', r'维修记录']),
        ('病害换算待确认', [r'换算.*运营部|运营部.*换算', r'尚未最终定版']),
        ('实时推送依赖', [r'实时推送.*工单|工单.*实时推送']),
        ('iServer主数据', [r'iServer', r'主数据.*iServer|iServer.*主数据']),
    ]),
    _sample('N5 阈值配置', FILES[2], 'N5 阈值配置读写', [
        ('内置默认非500', [r'内置.*默认|默认.*内置', r'回落.*default|source=default', r'空库']),
        ('400/422/409分离', [r'400.*422.*409|422.*409', r'结构.*语义.*版本']),
        ('500给残缺', [r'残缺', r'不完整', r'HTTP\s*500', r'code=500']),
        ('空白tunnelCode', [r'空白.*tunnelCode|tunnelCode.*空白', r'tunnelCode.*default']),
        ('悲观锁', [r'悲观锁', r'lockByTunnelCode']),
        ('JsonProperty', [r'JsonProperty', r'toutCm|tinCm']),
        ('tauMin派生', [r'tauMin', r'tauMultiplier']),
        ('版本冲突409', [r'版本冲突.*409|409.*版本冲突']),
        ('四行同事务', [r'同事务', r'四行', r'saveAll']),
        ('真实DB联调', [r'HighGo', r'真实库|真实.*联调|联调.*HighGo']),
    ]),
    _sample('N7 查询接口', FILES[3], None, [
        ('不动既有6接口', [r'不动.*既有|既有.*不动', r'6\s*个.*接口']),
        ('只读不现算', [r'现算|实时计算', r'只读']),
        ('空态失败态区分', [r'空态.*失败|失败.*空态', r'不得渲染成无积水']),
        ('reason_codes状态表', [r'reason_codes.*状态表|状态表.*reason_codes']),
        ('隧道名标量子查询', [r'标量子查询', r'JOIN.*乘开|重复.*JOIN']),
        ('排序写死SQL', [r'排序.*SQL|SQL.*排序', r'固定排序']),
        ('服务不catch', [r'不.*catch|catch.*不', r'上抛']),
        ('tunnelCodes≤500', [r'tunnelCodes.*500|500.*tunnelCodes']),
        ('六态枚举', [r'六态', r'RED.*ORANGE.*YELLOW|NORMAL.*MONITOR_ABNORMAL']),
    ]),
    _sample('N9 去假数据', FILES[2], 'N9 去假数据', [
        ('删nodeCards', [r'nodeCards', r'假预测']),
        ('未启用占位', [r'未启用.*占位|占位.*未启用', r'待模型']),
        ('阈值读真实接口', [r'tunnel-flood/thresholds', r'thresholds.*改读']),
        ('等级用后端level', [r'后端.*level|level.*后端', r'不做阈值判定']),
        ('色值唯一来源', [r'LEVEL_COLORS', r'色值.*唯一|唯一.*色值']),
        ('阈值来源可解释', [r'来源.*采样|采样.*来源', r'隧道级配置.*全局默认']),
        ('τ不写死', [r'tauMultiplier', r'τ.*写死|写死.*τ']),
        ('失败不假默认', [r'假默认|回退.*默认', r'thresholdError']),
        ('失败不无积水', [r'无积水', r'查询失败']),
        ('切隧道阈值跟随', [r'切换隧道|handleTunnelChange', r'过期响应|thresholdSeq']),
    ]),
]


def test_h5_replay_final_rate():
    r = replay(SAMPLES, extract_review_points, weak=FINAL_WEAK)
    assert r['golden_total'] == 49
    assert r['final_total'] == 22
    assert r['rate'] == 44
    by_name = {row['name']: row for row in r['rows']}
    assert by_name['N1 四级阈值']['final'] == 8
    assert by_name['需求19 管段健康度']['final'] == 8
    assert by_name['N5 阈值配置']['final'] == 1
    assert by_name['N7 查询接口']['final'] == 1
    assert by_name['N9 去假数据']['final'] == 4
