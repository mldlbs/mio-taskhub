# widget 系统托盘驻留 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 mio-taskhub 浮动面板驻留 Windows 系统托盘：关窗隐藏到托盘、托盘图标可主动打开面板、菜单可退出。

**Architecture:** `run_widget.py` 加 pystray 托盘图标（独立线程），pywebview `closing` 事件拦截关窗改为 `hide()`；托盘单击/菜单调 `window.show()`，菜单「退出」才真正销毁窗口退出。打包 spec 为 widget EXE 加 pystray/PIL hiddenimports 并从 widget 的 excludes 移除 PIL。

**Tech Stack:** Python 3.12 / pywebview / pystray 0.19.5 / PIL / PyInstaller

---

### Task 1: run_widget.py 托盘驻留

**Files:**
- Modify: `packaging/run_widget.py`

- [ ] **Step 1: 重构 run_widget.py**

Read `packaging/run_widget.py` fully first. Rewrite it to add tray support. Keep existing `_res_icon`, `_apply_icon`, `_hub_alive` unchanged. New structure:

```python
# mio-taskhub floating task center panel (置顶浮动任务中心)
# Run: python packaging/run_widget.py
import ctypes
import os
import socket
import sys
import threading

import webview

PORT = int(os.environ.get("MIO_TASKHUB_PORT", "48620"))
URL = f"http://127.0.0.1:{PORT}/"


def _res_icon() -> str:
    """解析 icon 路径：打包后取 _MEIPASS 内的资源，源码模式取 web/public。"""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        cand = os.path.join(base, "web", "public", "icon.ico")
        return cand if os.path.exists(cand) else ""
    cand = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web", "public", "icon.ico")
    return cand if os.path.exists(cand) else ""


ICO = _res_icon()

WM_SETICON = 0x0080
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
ICON_SMALL = 0
ICON_BIG = 1


def _apply_icon():
    """给窗口标题栏设置自定义图标（Windows）。"""
    if not ICO:
        return
    try:
        w = webview.windows[0]
        hwnd = w.native.Handle
        hico = ctypes.windll.user32.LoadImageW(None, ICO, IMAGE_ICON, 0, 0, LR_LOADFROMFILE)
        if hico:
            ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hico)
            ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hico)
    except Exception:
        pass


def _hub_alive() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def _start_tray(window, on_quit):
    """在独立线程启动系统托盘图标。返回 Icon 对象。

    - 单击/菜单「显示面板」→ window.show()
    - 菜单「退出」→ on_quit()（停托盘 + 销毁窗口）
    降级：pystray/PIL 不可用时不驻留托盘（保持现状行为）。
    """
    try:
        import pystray
        from PIL import Image
    except Exception:
        return None

    def _show(_icon=None, _item=None):
        try:
            window.show()
        except Exception:
            pass

    def _quit(_icon=None, _item=None):
        try:
            _icon.stop()
        except Exception:
            pass
        on_quit()

    try:
        if ICO:
            img = Image.open(ICO)
        else:
            img = Image.new("RGB", (32, 32), (61, 220, 151))
        icon = pystray.Icon(
            "mio-taskhub",
            img,
            "mio-taskhub · 任务中心",
            menu=pystray.Menu(
                pystray.MenuItem("显示面板", _show, default=True),
                pystray.MenuItem("退出", _quit),
            ),
        )
        t = threading.Thread(target=icon.run, daemon=True)
        t.start()
        return icon
    except Exception:
        return None


def main():
    if not _hub_alive():
        try:
            ctypes.windll.user32.MessageBoxW(
                0,
                f"mio-taskhub hub 未运行（127.0.0.1:{PORT}）。\n\n请先启动 mio-taskhub，再打开此面板。",
                "mio-taskhub",
                0x10,
            )
        except Exception:
            pass
        return

    window = webview.create_window(
        "mio-taskhub · 任务中心",
        URL,
        width=1080,
        height=720,
        min_size=(640, 480),
        resizable=True,
        on_top=True,
        background_color="#0f1115",
    )

    quit_flag = {"done": False}

    def _on_quit():
        quit_flag["done"] = True
        try:
            window.destroy()
        except Exception:
            pass

    # 拦截关窗：隐藏到托盘而非退出（若托盘可用）
    tray = _start_tray(window, _on_quit)
    if tray is not None:
        def _on_closing():
            window.hide()
            return False  # 取消关闭
        window.events.closing += _on_closing

    webview.start(func=_apply_icon)
    # webview.start 返回（窗口被 destroy 或进程退出）——若托盘还在则停止
    if tray is not None:
        try:
            tray.stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()
```

**关键点：**
- `window.events.closing += _on_closing`：closing 事件返回 `False` 取消关闭（pywebview 支持）。`_on_closing` 里 `window.hide()` 隐藏窗口。
- `_start_tray` 返回 `None`（pystray/PIL 不可用）时**不注册 closing 拦截**，保持现状（关窗即退出）。
- 托盘线程 daemon=True，进程退出时随主线程结束。
- `_on_quit` 设 `quit_flag` 并 `window.destroy()`——destroy 后 `webview.start()` 返回，主流程 stop 托盘，进程结束。

- [ ] **Step 2: 语法检查 + import 检查**

Run: `python -c "import ast; ast.parse(open(r'packaging/run_widget.py', encoding='utf-8').read()); print('syntax OK')"`
Expected: `syntax OK`

- [ ] **Step 3: 全量回归（后端不受影响）**

Run: `python -m pytest tests/ -q`
Expected: PASS（216，无后端改动）

- [ ] **Step 4: 冒烟启动验证**

Run: `Start-Process python -ArgumentList "packaging/run_widget.py" -WorkingDirectory "E:\work\code\agent-dev\mio-taskhub"`（后台启动，hub 48620 在运行）。
等待 4 秒后 `Get-Process python | Where CommandLine -match run_widget` 确认进程存活（窗口 + 托盘线程）。
然后 `Stop-Process` 清理。

注：完整 GUI 交互（关窗隐藏/托盘点开/菜单退出）需人工验证，本步骤只确认能启动且不崩溃。

- [ ] **Step 5: Commit**

```bash
git add packaging/run_widget.py
git commit -m "feat: widget 托盘驻留（pystray + closing 拦截隐藏）"
```

### Task 2: 打包 spec 更新

**Files:**
- Modify: `mio-taskhub.spec`
- Modify: `packaging/mio-taskhub.spec`

- [ ] **Step 1: Update mio-taskhub.spec widget Analysis**

Read `mio-taskhub.spec`. The `widget_hiddenimports` list currently has `["webview", "clr", "pythonnet"] + collect_submodules("webview")`. Add pystray/PIL:

```python
widget_hiddenimports = (
    ["webview", "clr", "pythonnet", "pystray", "PIL", "PIL.Image"]
    + collect_submodules("webview")
)
```

The widget `Analysis` uses `excludes=excludes` where `excludes` contains `"PIL"`. Since the widget needs PIL for the tray icon, create a widget-specific excludes without PIL:

```python
widget_excludes = [x for x in excludes if x != "PIL"]
```

And change the widget Analysis to use `widget_excludes`:

```python
a_widget = Analysis(
    ["packaging/run_widget.py"],
    pathex=[SPECPATH],
    binaries=[],
    datas=[(os.path.join(SPECPATH, "web", "public", "icon.ico"), "web/public")],
    hiddenimports=widget_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=widget_excludes,
    noarchive=False,
    optimize=0,
)
```

- [ ] **Step 2: Update packaging/mio-taskhub.spec widget Analysis**

Read `packaging/mio-taskhub.spec`. Its `widget_hiddenimports` also needs pystray/PIL:

```python
widget_hiddenimports = (
    ["webview", "clr", "pythonnet", "pystray", "PIL", "PIL.Image"]
    + collect_submodules("webview")
)
```

Its widget Analysis has `excludes=[]` (no PIL exclusion), so no change needed there beyond hiddenimports. Verify by reading.

- [ ] **Step 3: 重打 widget EXE**

Run: `python -m PyInstaller mio-taskhub.spec --noconfirm`
Expected: build completes; `dist/mio-taskhub/mio-taskhub-widget.exe` exists; `_internal/pystray` and `_internal/PIL` present.

Verify:
```powershell
Get-ChildItem "dist\mio-taskhub\_internal" | Where-Object { $_.Name -match "pystray|PIL" }
```
Expected: both present.

- [ ] **Step 4: widget EXE 冒烟测试**

后台启动 `dist/mio-taskhub/mio-taskhub-widget.exe`，等 6 秒确认进程存活（窗口 + 托盘），再 Stop-Process。
```powershell
$p = Start-Process "E:\work\code\agent-dev\mio-taskhub\dist\mio-taskhub\mio-taskhub-widget.exe" -PassThru
Start-Sleep -Seconds 6
if ($p.HasExited) { "EXITED code=$($p.ExitCode)" } else { "running OK PID=$($p.Id)"; Stop-Process $p.Id -Force }
```

- [ ] **Step 5: Commit**

```bash
git add mio-taskhub.spec packaging/mio-taskhub.spec
git commit -m "build: widget EXE 打包 pystray/PIL（托盘）"
```

### Task 3: 使用说明 + 记忆更新

**Files:**
- Modify: `packaging/使用说明.txt`
- Modify: `docs/memory-workbench-p0-2026-08-15.md`

- [ ] **Step 1: Update 使用说明.txt widget 节**

Read `packaging/使用说明.txt`. In the【四】浮动任务中心面板 section, add tray behavior:

```
【四】浮动任务中心面板（可选）
  - 想边工作边看任务进度？双击【mio-taskhub-widget.exe】
  - 会出现一个置顶的迷你任务看板窗口，随时瞄一眼
  - 点窗口关闭按钮（X）不会退出，而是收进系统托盘（右下角小图标）
  - 点托盘图标 → 重新打开面板；右键托盘图标 → 「显示面板」/「退出」
  - 托盘菜单选「退出」才真正结束面板（不影响 mio-taskhub.exe 主服务）
  - 可拖拽、可缩放；前提：【mio-taskhub.exe】必须已在运行
```

- [ ] **Step 2: Update memory doc**

`docs/memory-workbench-p0-2026-08-15.md` 追加：

```
- widget 系统托盘驻留：pystray 托盘图标（关窗隐藏、单击打开、菜单退出），打包含 pystray/PIL hiddenimports；spec `docs/superpowers/specs/2026-08-15-widget-tray-design.md`
```

- [ ] **Step 3: Commit**

```bash
git add packaging/使用说明.txt docs/memory-workbench-p0-2026-08-15.md
git commit -m "docs: widget 托盘驻留使用说明"
```
