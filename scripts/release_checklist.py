# -*- coding: utf-8 -*-
"""发布前 checklist 全绿校验（T4.3 / G4 收口）。

7 项发布门槛，逐项验证并输出 是/否/部分：
  ① CI 全绿          ② 无已知红（0 failed）   ③ 许可合规（LICENSE MIT）
  ④ README 一致      ⑤ 首跑达标（干净机+Quickstart）  ⑥ 有 Release（tag+资产+SHA256+changelog）
  ⑦ 已知限制（能力地图无待定）

用法：
  python scripts/release_checklist.py            # 人类可读
  python scripts/release_checklist.py --json     # 机器可读
  python scripts/release_checklist.py --ci       # 附加查询 GitHub CI（需 gh）
"""
from __future__ import annotations
import json
import re
import subprocess
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent


def _run(cmd, cwd=ROOT, timeout=300):
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def check_ci(with_ci: bool) -> dict:
    if not with_ci:
        return {"item": "① CI 全绿", "status": "未查", "detail": "传 --ci 查询 GitHub Actions"}
    try:
        r = _run(["gh", "run", "list", "--limit", "1", "--json", "conclusion,databaseId"], timeout=60)
        if r.returncode != 0:
            return {"item": "① CI 全绿", "status": "未查", "detail": f"gh 失败: {r.stderr[:120]}"}
        data = json.loads(r.stdout or "[]")
        if not data:
            return {"item": "① CI 全绿", "status": "未查", "detail": "无 CI run"}
        ok = data[0].get("conclusion") == "success"
        return {"item": "① CI 全绿", "status": "是" if ok else "否",
                "detail": f"最新 run {data[0].get('databaseId')} = {data[0].get('conclusion')}"}
    except Exception as e:
        return {"item": "① CI 全绿", "status": "未查", "detail": str(e)[:120]}


def check_no_red() -> dict:
    r = _run([sys.executable, "-m", "pytest", "tests/", "-q", "--no-header",
              "-p", "no:cacheprovider"], timeout=1200)
    out = r.stdout + r.stderr
    m = re.search(r"(\d+) failed", out)
    failed = int(m.group(1)) if m else 0
    return {"item": "② 无已知红", "status": "是" if failed == 0 else "否",
            "detail": f"failed={failed}"}


def check_license() -> dict:
    r = _run(["git", "ls-files", "LICENSE"])
    tracked = r.stdout.strip() == "LICENSE"
    mit = False
    p = ROOT / "LICENSE"
    if p.exists():
        mit = "MIT License" in p.read_text(encoding="utf-8")
    return {"item": "③ 许可合规", "status": "是" if (tracked and mit) else "否",
            "detail": f"tracked={tracked} MIT={mit}"}


def check_readme() -> dict:
    r = _run([sys.executable, "scripts/gen_readme_stats.py", "--check"], timeout=120)
    ok = r.returncode == 0
    return {"item": "④ README 一致", "status": "是" if ok else "否",
            "detail": "gen_readme_stats --check " + ("OK" if ok else r.stdout[-200:])}


def check_first_run() -> dict:
    report = ROOT / "docs" / "roadmap" / "t35-coldstart-report.md"
    if not report.exists():
        return {"item": "⑤ 首跑达标", "status": "否", "detail": "缺冷启报告"}
    text = report.read_text(encoding="utf-8")
    local_ok = "PASS" in text and "n=5" in text
    external = "待人工取证" not in text
    return {"item": "⑤ 首跑达标", "status": "是" if (local_ok and external) else "部分",
            "detail": f"本机n=5={'PASS' if local_ok else '?'}; 外部3台={'已' if external else '待人工取证'}"}


def check_release() -> dict:
    tag = _run(["git", "tag", "-l"], timeout=30).stdout.split()
    ver = None
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        from gen_readme_stats import _version
        ver = _version()
    except Exception:
        pass
    has_tag = f"v{ver}" in tag if ver else False
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    has_changelog = f"## v{ver}" in changelog if ver else False
    report = ROOT / "docs" / "roadmap" / "t42-repro-build-report.md"
    has_sha = report.exists() and "SHA256" in report.read_text(encoding="utf-8")
    ok = has_tag and has_changelog and has_sha
    return {"item": "⑥ 有 Release", "status": "是" if ok else "部分",
            "detail": f"tag={has_tag} changelog={has_changelog} sha256={has_sha}"}


def check_known_limits() -> dict:
    capmap = ROOT / "docs" / "taskhub" / "capability-map-20261001.md"
    if not capmap.exists():
        return {"item": "⑦ 已知限制", "status": "否", "detail": "缺能力地图"}
    text = capmap.read_text(encoding="utf-8")
    no_pending = "待定" not in text or "0 项待定" in text
    has_verdict = "逐项定档" in text
    ok = no_pending and has_verdict
    return {"item": "⑦ 已知限制", "status": "是" if ok else "否",
            "detail": f"逐项定档={has_verdict} 无待定={no_pending}"}


def check_installer_bundle() -> dict:
    """安装器分发产物一致性（安装器 exe ↔ payload zip ↔ 独立安装包目录）。

    实测踩过（2026-10-21）：重建了 uninstall.exe 却没重打 payload zip →
    用户装到的是旧卸载器 → 静默卸载异常退出（rc=23、目录还在）。
    """
    r = _run([sys.executable, "scripts/check_installer_bundle.py"], timeout=300)
    ok = r.returncode == 0
    detail = (r.stdout or r.stderr or "").strip().splitlines()
    tail = " / ".join(detail[-2:]) if detail else ""
    return {"item": "⑧ 安装器产物一致", "status": "是" if ok else "否",
            "detail": tail[:200]}


def main():
    with_ci = "--ci" in sys.argv
    results = [
        check_ci(with_ci),
        check_no_red(),
        check_license(),
        check_readme(),
        check_first_run(),
        check_release(),
        check_known_limits(),
        check_installer_bundle(),
    ]
    all_green = all(r["status"] == "是" for r in results)
    if "--json" in sys.argv:
        print(json.dumps({"all_green": all_green, "items": results}, ensure_ascii=False, indent=2))
    else:
        print("发布前 checklist：")
        for r in results:
            mark = {"是": "[YES]", "否": "[NO ]", "部分": "[PART]", "未查": "[SKIP]"}[r["status"]]
            print(f"  {mark} {r['item']}: {r['detail']}")
        print(f"\n全绿 = {all_green}  →  {'可发布' if all_green else '不发布（任一项不达标）'}")
    return 0 if all_green else 1


if __name__ == "__main__":
    sys.exit(main())
