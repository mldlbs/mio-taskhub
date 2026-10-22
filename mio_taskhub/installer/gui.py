# -*- coding: utf-8 -*-
"""安装向导 / 卸载向导的 GUI（Tkinter）。

设计原则：
- **不依赖网络**：安装包在本地 zip 里，安装过程纯本地操作。
- **可静默**：`--silent` / `/S` 走 CLI 路径（CI、批量部署用）。
- **失败要说人话**：每一步的结果都显示在界面上，失败时给出原因与建议。

视觉：与 Web UI 同源（深色底 #0b0e13 + 荧光绿主色 #cdfa3f），但**不套用 ttk 默认控件** ——
原生 Checkbox / Entry 在深色底上会露出浅色系统色，看着像拼贴。
因此输入框、按钮、复选框一律用 tk.Canvas 自绘，保证同一套设计语言。

Tkinter 两个绕不开的限制（不处理就会被用户看见）：
1. `console=False` 打包后无控制台 → 标题栏由 Windows 画，浅色主题改不了；
   靠**内容区的深色卡片**把视觉重心拉回界面内。
2. 模态弹窗（messagebox）在部分 Win10 上标题栏会闪「(未响应)」——
   那是模态循环的正常表现，但很吓人。**改用界面内自绘的提示条**。
"""
from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog

from .install import detect_install_dir, install, uninstall
from .paths import (
    APP_DISPLAY_NAME,
    app_version,
    data_dir,
    default_install_dir,
)

# ── 设计令牌（对齐 web/src/index.css 的 :root）──────────────────────────
BG = "#0b0e13"          # 页面底
BG2 = "#11161d"         # 次级底
CARD = "#1a222d"        # 卡片
CARD_HI = "#212b38"     # 卡片 hover
FG = "#e6edf3"          # 主文字
MUTED = "#8b949e"       # 次要文字
ACCENT = "#cdfa3f"      # 主色（荧光绿）
ACCENT_INK = "#0b0e11"  # 主色上的文字
ACCENT_SOFT = "#232c17"  # 主色弱化底
LINE = "#263041"        # 分隔线
DANGER = "#f85149"
DANGER_SOFT = "#3a1f1f"
OK = "#3fb950"

FONT = "Microsoft YaHei UI"
MONO = "Consolas"


def _font(size=10, weight="normal"):
    return (FONT, size, weight)


def _rounded_points(x1, y1, x2, y2, r):
    """圆角矩形的多边形顶点（Canvas 无原生圆角，用平滑多边形逼近）。"""
    return [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2,
            x1 + r, y2, x1, y2, x1, y2 - r,
            x1, y1 + r, x1, y1, x1 + r, y1]


class _Button(tk.Canvas):
    """自绘按钮：深色底 / 描边 / 主色实心，避免 ttk 默认观感。"""

    def __init__(self, master, text, command=None, kind="ghost",
                 width=112, height=38, parent_bg=BG, **kw):
        self._kind = kind
        self._text = text
        self._command = command
        self._enabled = True
        self._hover = False
        super().__init__(master, width=width, height=height, highlightthickness=0,
                         bd=0, bg=parent_bg, cursor="hand2", **kw)
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    def _on_enter(self, _e):
        self._hover = self._enabled
        self._draw()

    def _on_leave(self, _e):
        self._hover = False
        self._draw()

    def _on_press(self, _e):
        if self._enabled:
            self._draw(pressed=True)

    def _on_release(self, e):
        self._draw()
        if (self._enabled and self._command
                and 0 <= e.x <= self.winfo_width()
                and 0 <= e.y <= self.winfo_height()):
            self._command()

    def set_enabled(self, enabled: bool):
        self._enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        self._draw()

    def set_text(self, text: str):
        self._text = text
        self._draw()

    def _colors(self):
        if not self._enabled:
            return (CARD, LINE, MUTED)
        if self._kind == "primary":
            return (ACCENT, ACCENT, ACCENT_INK)
        if self._kind == "danger":
            return (DANGER, DANGER, "#ffffff")
        if self._kind == "secondary":
            # 次级按钮：比 ghost 亮一档，边框更清晰（避免像浮在背景上的文字）
            return (BG2 if not self._hover else CARD_HI,
                    ACCENT if self._hover else "#3a4657", FG)
        if self._hover:
            return (CARD_HI, ACCENT, FG)
        return (CARD, LINE, FG)

    def _draw(self, pressed=False):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1:
            w, h = int(self["width"]), int(self["height"])
        bg, border, fg = self._colors()
        if pressed and self._enabled:
            bg = ACCENT_SOFT if self._kind == "primary" else CARD_HI
        self.create_polygon(_rounded_points(2, 2, w - 3, h - 3, 6),
                            fill=bg, outline=border, width=1, smooth=True)
        self.create_text(w / 2, h / 2, text=self._text, fill=fg,
                         font=_font(10, "bold" if self._kind == "primary" else "normal"))


class _Field(tk.Canvas):
    """自绘输入框：带描边 + 占位符，看起来就是可编辑的输入框。

    `display_basename=True` 时只显示文件名（如 `mio-taskhub-win64.zip`），
    真实路径在悬停提示里 —— 面向终端用户时把 `E:\\work\\code\\...`
    这种开发路径摆在眼前，会让人以为这是内部工具没做完。

    注意：显示用**独立的 StringVar**（`_display_var`）。Entry 若直接绑真实
    变量，往里 insert 文本会**回写污染真实路径**。
    """

    def __init__(self, master, textvariable, placeholder="", width=380,
                 height=36, parent_bg=CARD, display_basename=False, **kw):
        self._var = textvariable
        self._placeholder = placeholder
        self._hover = False
        self._focus = False
        self._display_basename = display_basename
        self._display_var = tk.StringVar()
        self._tip = None
        super().__init__(master, width=width, height=height, highlightthickness=0,
                         bd=0, bg=parent_bg, cursor="xterm", **kw)
        self._entry = tk.Entry(self, textvariable=self._display_var, bg=BG2,
                               fg="#e8eef5", insertbackground=ACCENT, relief="flat",
                               bd=0, highlightthickness=0, font=_font(10),
                               selectbackground="#2b3949", selectforeground=FG)
        self._entry_window = self.create_window(10, height / 2, window=self._entry,
                                                 anchor="w")
        self._entry.bind("<FocusIn>", lambda e: self._set_focus(True))
        self._entry.bind("<FocusOut>", lambda e: self._set_focus(False))
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Motion>", self._on_motion)
        self.bind("<Button-1>", lambda e: self._entry.focus_set())
        self._draw()

    def _on_enter(self, _e):
        self._hover = True
        self._draw()

    def _on_leave(self, _e):
        self._hover = False
        self._hide_tip()
        self._draw()

    def _set_focus(self, state):
        self._focus = state
        self._draw()

    def _draw(self):
        # 只删图形元素，**不能 delete("all")** —— 那会把 create_window 创建的
        # Entry 一并删掉（Entry 变成 1x1 隐藏，路径框看着是空的）。
        self.delete("rounded")
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1:
            w, h = int(self["width"]), int(self["height"])
        border = ACCENT if self._focus else (LINE if self._hover else "#2b3748")
        self.create_polygon(_rounded_points(1, 1, w - 2, h - 2, 6),
                            fill=BG2, outline=border, width=1, smooth=True,
                            tags="rounded")
        self.coords(self._entry_window, 10, h / 2)
        full = self._var.get()
        if self._display_basename:
            # 显示友好名（只保留末两段），完整路径走悬停提示
            self._display_var.set(self._friendly_name(full))
            self.configure(cursor="hand2")
        else:
            self._display_var.set(full)
        # Entry 是**不透明**的，嵌在 Canvas 上会盖住占位符文字（只剩尾巴）。
        # 空值时把 Entry 隐藏，让占位符完整显示。
        try:
            self.itemconfigure(self._entry_window,
                               state="hidden" if not full else "normal")
        except Exception:  # noqa: BLE001
            pass
        if not full and self._placeholder:
            self.create_text(11, h / 2, text=self._placeholder, fill="#5b6675",
                             anchor="w", font=_font(10), tags="rounded")

    @staticmethod
    def _friendly_name(full: str) -> str:
        """路径 → 友好显示：保留末两段（`…\\Programs\\mio-taskhub`）。

        只给文件名会丢掉「装在哪一级」的语境；给完整路径又太长会被截断。
        """
        if not full:
            return ""
        parts = [p for p in full.replace("/", "\\").split("\\") if p]
        if len(parts) <= 2:
            return full
        return "…\\" + "\\".join(parts[-2:])

    def _on_motion(self, e):
        """悬停显示完整路径（Tooltip）。已存在则只跟随，不重复创建。"""
        if not self._display_basename:
            return
        full = self._var.get()
        if not full:
            self._hide_tip()
            return
        try:
            x = self.winfo_pointerx() + 12
            y = self.winfo_pointery() + 18
            if self._tip is None:
                self._tip = tk.Toplevel(self)
                self._tip.wm_overrideredirect(True)
                tk.Label(self._tip, text=full, bg="#0a0d12", fg="#c9d1d9",
                         font=_font(9), padx=8, pady=4, bd=0,
                         highlightthickness=1, highlightbackground=LINE).pack()
            else:
                self._tip.geometry("+%d+%d" % (x, y))
        except Exception:  # noqa: BLE001
            pass

    def _hide_tip(self, _e=None):
        tip = getattr(self, "_tip", None)
        if tip is not None:
            try:
                tip.destroy()
            except Exception:  # noqa: BLE001
                pass
            self._tip = None


class _Check(tk.Canvas):
    """自绘复选框：深色底 + 主色勾，避免原生浅色控件。"""

    def __init__(self, master, text, variable, parent_bg=CARD, **kw):
        self._var = variable
        self._text = text
        self._hover = False
        super().__init__(master, height=24, highlightthickness=0, bd=0,
                         bg=parent_bg, cursor="hand2", **kw)
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", lambda e: self._toggle())
        self._draw()

    def _on_enter(self, _e):
        self._hover = True
        self._draw()

    def _on_leave(self, _e):
        self._hover = False
        self._draw()

    def _toggle(self):
        self._var.set(not self._var.get())
        self._draw()

    def _draw(self):
        self.delete("all")
        on = bool(self._var.get())
        size = 16
        y = (self.winfo_height() or 24) / 2
        self.create_polygon(
            _rounded_points(1, y - size / 2, 1 + size, y + size / 2, 4),
            fill=ACCENT if on else BG2,
            outline=ACCENT if on else (LINE if self._hover else "#2a3444"),
            width=1, smooth=True)
        if on:
            self.create_line(5, y, 8, y + 3.5, 13.5, y - 3.5, fill=ACCENT_INK,
                             width=2, capstyle="round", joinstyle="round")
        self.create_text(size + 10, y, text=self._text, fill="#dbe3ec", anchor="w",
                         font=_font(10))


class _Notice(tk.Frame):
    """界面内提示条（替代 messagebox —— 模态框在 Win10 上会闪「(未响应)」）。"""

    def __init__(self, master, **kw):
        super().__init__(master, bg=BG, **kw)
        self._card = tk.Canvas(self, height=52, highlightthickness=0, bd=0, bg=BG)
        self._kind = "ok"
        self._title = ""
        self._body = ""
        self.visible = False
        self._card.bind("<Configure>", lambda e: self._draw())

    def show(self, kind, title, body=""):
        self._kind = kind
        self._title = title
        self._body = body
        self.visible = True
        if not self._card.winfo_ismapped():
            self._card.pack(fill="x", pady=(0, 12))
        self._draw()

    def hide(self):
        self.visible = False
        self._card.pack_forget()

    def _draw(self):
        self._card.delete("all")
        w = max(self._card.winfo_width(), 300)
        color = {"ok": OK, "err": DANGER, "info": ACCENT}.get(self._kind, ACCENT)
        soft = {"ok": "#12261a", "err": DANGER_SOFT, "info": ACCENT_SOFT}.get(
            self._kind, ACCENT_SOFT)
        self._card.create_polygon(_rounded_points(1, 1, w - 2, 50, 8),
                                  fill=soft, outline=color, width=1, smooth=True)
        self._card.create_text(14, 18, text=self._title, fill=color,
                               anchor="w", font=_font(10, "bold"))
        if self._body:
            self._card.create_text(14, 36, text=self._body, fill=MUTED,
                                   anchor="w", font=_font(9))


class _LogPane(tk.Frame):
    """日志区：**默认不占位**（不装时看不到），安装时才展开。

    高度**固定**（不随行数增长）—— 否则日志一行行输出会把窗口越撑越大。
    超出部分用滚动条看。
    """

    LOG_H = 130

    def __init__(self, master, parent_bg=BG, **kw):
        super().__init__(master, bg=parent_bg, **kw)
        self._text = tk.Text(self, height=6, bg=BG2, fg="#c9d1d9",
                             insertbackground=FG, relief="flat", bd=0,
                             font=(MONO, 9), wrap="word", padx=10, pady=8,
                             highlightthickness=0)
        # 固定高度 + 内部滚动条：日志再多也不改变窗口尺寸。
        sb = tk.Scrollbar(self, orient="vertical", command=self._text.yview,
                           bg=BG2, troughcolor=BG, activebackground=LINE,
                           relief="flat", bd=0, width=10)
        self._text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._text.pack(side="left", fill="both", expand=True)
        self._text.configure(state="disabled")
        self.visible = False

    def show(self):
        if not self.visible:
            # **固定高度** → 展开时窗口尺寸不变
            self.pack(fill="x", expand=False, pady=(12, 0))
            self.configure(height=self.LOG_H)
            self.pack_propagate(False)
            self.visible = True
            # 日志区展开会让 Tk 想把窗口撑大 → 重新钉死尺寸。
            owner = getattr(self, "_owner", None)
            if owner is not None:
                try:
                    owner._lock_size()
                except Exception:  # noqa: BLE001
                    pass

    def append(self, text: str):
        self.show()
        self._text.configure(state="normal")
        self._text.insert("end", text + "\n")
        self._text.see("end")
        self._text.configure(state="disabled")


def _label(parent, text, size=10, color=FG, weight="normal", bg=BG, **kw):
    return tk.Label(parent, text=text, bg=bg, fg=color, font=_font(size, weight), **kw)


def _ui_call(root, fn, *args):
    """从后台线程安全地回到 UI 线程执行 fn（**兼容旧调用方式**）。

    `root.after()` 在主线程**不在 mainloop** 时会抛
    `RuntimeError: main thread is not in main loop`。那个异常发生在工作线程里，
    界面会永远卡在「正在安装/卸载」—— 用户看不出发生了什么。

    这里吞掉该异常并记日志：真实运行（mainloop 在跑）不受影响，
    异常场景下至少留下线索，而不是静默卡死。

    **新代码请用 `_Base.post()`** —— 那是队列式交接，完全不碰 Tk 线程模型。
    """
    try:
        root.after(0, fn, *args)
    except RuntimeError as e:  # noqa: BLE001
        try:
            from .entry_support import log_path
            with open(log_path("installer.log"), "a", encoding="utf-8") as f:
                f.write("[ui-dispatch-failed] %s: %s\n" % (getattr(fn, "__name__", fn), e))
        except Exception:  # noqa: BLE001
            pass


class _Base:
    """安装/卸载向导的公共外壳：品牌头 + 内容区 + 底部操作条。"""

    #: 窗口固定尺寸（客户区，不含标题栏/边框）
    #:
    #: **固定而不是「随内容伸缩」**：日志面板展开时会给窗口加一块高度，
    #: 若不锁死尺寸，用户会看到窗口「自己变大了」—— 显得不专业，
    #: 而且展开前后的按钮位置会跳。
    #:
    #: 宽度 620：路径框能显示更多内容（原来 466 太窄）。
    #: 高度 430：装完/卸载时日志面板占 150px，仍有余量；
    #: 再高就会出现明显的大片空白（显得头重脚轻）。
    WINDOW_W = 620
    WINDOW_H = 430

    def __init__(self, root, title, subtitle):
        self.root = root
        self._closed = False
        self._jobs = []          # 自行排程的 after 回调（销毁时统一 cancel）
        # 工作线程 → 主线程的**队列式交接**。
        # 为什么不用 root.after()：那是从**工作线程**调 Tk 的线程模型，
        # 主线程不在 mainloop 时直接抛 RuntimeError；而且 Tk 本身不保证
        # 跨线程安全。队列 + 主线程轮询是唯一稳妥的做法。
        self._ui_queue = queue.Queue()
        root.title(title)
        root.configure(bg=BG)
        # 固定尺寸：用户不能拖拽改变，日志展开时窗口也不变。
        root.resizable(False, False)
        root.minsize(self.WINDOW_W, self.WINDOW_H)
        root.maxsize(self.WINDOW_W, self.WINDOW_H)
        # 拦截窗口关闭：走 _close_now 把 _closed 置位。
        # 否则 root.destroy() 之后，Python 侧排队的 after 回调仍会执行，
        # 报 "invalid command name ... (after script)"。
        try:
            root.protocol("WM_DELETE_WINDOW", self._close_now)
        except Exception:  # noqa: BLE001
            pass
        # 安装/卸载在后台线程跑，主线程此时可能长时间无消息处理 →
        # Windows 会把标题栏标成「(未响应)」。周期性喂消息避免误报。
        self._pulse()

        self._shell = tk.Frame(root, bg=BG)
        self._shell.pack(fill="both", expand=True, padx=28, pady=24)

        # 品牌头
        head = tk.Frame(self._shell, bg=BG)
        head.pack(fill="x")
        badge = tk.Canvas(head, width=44, height=44, highlightthickness=0, bd=0, bg=BG)
        badge.pack(side="left", padx=(0, 14))
        badge.create_oval(2, 2, 42, 42, fill=ACCENT, outline="")
        badge.create_text(22, 23, text="M", fill=ACCENT_INK, font=_font(20, "bold"))
        titles = tk.Frame(head, bg=BG)
        titles.pack(side="left", anchor="w")
        _label(titles, APP_DISPLAY_NAME, 15, FG, "bold").pack(anchor="w")
        _label(titles, subtitle, 9, "#98a3b0").pack(anchor="w", pady=(3, 0))

        # 内容区
        self.body = tk.Frame(self._shell, bg=BG)
        self.body.pack(fill="both", expand=True, pady=(20, 0))

        # 提示条（默认隐藏）
        self.notice = _Notice(self.body)
        self.notice.hide()

        # 底部操作条
        self.footer = tk.Frame(self._shell, bg=BG)
        self.footer.pack(fill="x", pady=(18, 0))
        self.status = _label(self.footer, "", 9, "#9aa5b1")
        self.status.pack(side="left")

        # **显式锁定窗口尺寸**（放在所有控件建完之后）：
        # Tk 默认按内容自动算尺寸，日志面板一展开窗口就变大。
        # 这里直接指定客户区大小，并让 shell 撑满 → 内容被约束在固定框里。
        self._lock_size()

    def _lock_size(self) -> None:
        """把窗口钉死在 WINDOW_W × WINDOW_H。

        - `geometry` 指定客户区尺寸（Windows 上还要加上标题栏/边框）
        - `update_idletasks()` 先算一次布局，再重设一次 —— 否则
          Tk 会用「内容算出来的尺寸」覆盖我们指定的值。
        - 之后 minsize=maxsize，用户也拖不动。
        """
        try:
            self.root.update_idletasks()
            self.root.geometry("%dx%d" % (self.WINDOW_W, self.WINDOW_H))
            self.root.update_idletasks()
            # 再钉一次：布局完成后 Tk 可能又按内容改过尺寸
            self.root.geometry("%dx%d" % (self.WINDOW_W, self.WINDOW_H))
            self.root.minsize(self.WINDOW_W, self.WINDOW_H)
            self.root.maxsize(self.WINDOW_W, self.WINDOW_H)
        except Exception:  # noqa: BLE001
            pass

    def post(self, fn, *args) -> None:
        """**工作线程专用**：把一个待执行的 UI 操作投进队列。

        工作线程只调这个方法，**不碰任何 Tk 对象**（不读变量、
        不调 root.after）。主线程在 `_pulse` 里把队列取出来执行。
        """
        self._ui_queue.put((fn, args))

    def _drain_queue(self) -> None:
        """主线程：把队列里的 UI 操作取出来执行。"""
        while True:
            try:
                fn, args = self._ui_queue.get_nowait()
            except queue.Empty:
                return
            try:
                fn(*args)
            except Exception as e:  # noqa: BLE001
                # 单个 UI 回调出错不能影响后续队列项
                try:
                    from .entry_support import log_path
                    with open(log_path("installer.log"), "a", encoding="utf-8") as f:
                        f.write("[ui-callback-failed] %r\n" % e)
                except Exception:  # noqa: BLE001
                    pass

    def _pulse(self):
        """周期性处理待处理事件（让 Windows 知道窗口还活着，不显示「未响应」）。

        `update_idletasks()` 只处理重绘类空闲事件，**不处理用户输入**；
        窗口长时间没有输入事件时 Windows 仍可能判定「未响应」。
        这里用 `update()`（含输入），并加 try 防止销毁期异常。

        同时把工作线程投递的 UI 操作取出来执行（队列式交接）。
        """
        if self._closed:
            return
        try:
            self._drain_queue()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.root.update()
        except Exception:  # noqa: BLE001 —— 窗口已销毁时忽略
            self._closed = True
            return
        try:
            self._jobs.append(self.root.after(300, self._pulse))
        except Exception:  # noqa: BLE001
            pass

    def _close_later(self, delay_ms: int = 1200) -> None:
        """延时关闭窗口。窗口若已销毁则不再排程（避免 after 脚本报错）。"""
        if self._closed:
            return
        try:
            self._jobs.append(self.root.after(delay_ms, self._close_now))
        except Exception:  # noqa: BLE001
            pass

    def _close_now(self) -> None:
        """关闭窗口，并把 _closed 置位。

        Tk 的 `after` 队列在窗口销毁后仍持有已删除的 Tcl 命令名，
        残留回调执行时会报 "invalid command name ... (after script)"。
        销毁前先 `after_cancel` 掉自己排程的回调，保持干净退出。
        """
        if self._closed:
            return
        self._closed = True
        for job in list(getattr(self, "_jobs", [])):
            try:
                self.root.after_cancel(job)
            except Exception:  # noqa: BLE001
                pass
        self._jobs = []
        try:
            self.root.destroy()
        except Exception:  # noqa: BLE001
            pass

    def _card(self, parent):
        """带描边的深色卡片：返回 (wrapper, content_frame)。"""
        wrap = tk.Frame(parent, bg=BG)
        inner = tk.Canvas(wrap, height=1, highlightthickness=0, bd=0, bg=BG)
        inner.pack(fill="both", expand=True)
        body = tk.Frame(wrap, bg=CARD)

        def _resize(_e=None):
            w = inner.winfo_width()
            h = max(inner.winfo_reqheight(), body.winfo_reqheight() + 28)
            inner.configure(height=h)
            inner.delete("all")
            inner.create_polygon(_rounded_points(1, 1, w - 2, h - 2, 10),
                                 fill=CARD, outline=LINE, width=1, smooth=True)
            body.place(x=16, y=14, width=max(w - 32, 10), height=max(h - 28, 10))

        inner.bind("<Configure>", _resize)
        wrap._inner = inner
        wrap._body = body
        return wrap, body


class InstallerApp(_Base):
    """安装向导。"""

    def __init__(self, root: tk.Tk, payload_zip=None):
        super().__init__(root, "%s 安装程序" % APP_DISPLAY_NAME,
                         "版本 %s · 无需安装 Python · 仅安装到本机" % app_version())
        self.payload_zip = payload_zip
        self.var_payload = tk.StringVar(value=payload_zip or "")
        self.var_dir = tk.StringVar(value=str(default_install_dir()))
        self.var_shortcuts = tk.BooleanVar(value=True)
        self._done = False
        self._build()
        self.root.after(80, self._auto_find_payload)

    def _build(self):
        # 「安装包」这一行**默认不显示**。
        # 真实分发时 setup.exe 与 payload zip 在同一目录 → 自动找到，用户无需操作。
        # 让人去「选安装包」是多余的一步（还会让人困惑「这必须吗？我该选什么？」）。
        # 只有**自动定位失败**时才把这一行显示出来，作为兜底入口。
        #
        # 放在**卡片外面**的独立槽位里：卡片内容区是用 place() 布局的，
        # 在里面 pack() 不会生效（控件会看不见）。槽位用 pack 挂到卡片**之前**，
        # 顺序靠 pack 顺序确定（不用 pack(before=...)，那会引用到未 pack 的控件）。
        self._payload_slot = tk.Frame(self.body, bg=BG)
        self._payload_slot.pack(fill="x", pady=(0, 12))
        _label(self._payload_slot, "未找到安装包，请手动选择", 9, "#e3b341",
               bg=BG).pack(anchor="w")
        row1 = tk.Frame(self._payload_slot, bg=BG)
        row1.pack(fill="x", pady=(6, 0))
        row1.columnconfigure(0, weight=1)
        self._field_payload = _Field(row1, self.var_payload,
                                     placeholder="请选择安装包文件（mio-taskhub-win64.zip）",
                                     parent_bg=BG, display_basename=True)
        self._field_payload.grid(row=0, column=0, sticky="ew")
        _Button(row1, "选择文件…", self._browse_payload, kind="secondary", width=88,
                height=36, parent_bg=BG).grid(row=0, column=1, sticky="e",
                                              padx=(10, 0))
        self._payload_slot.pack_forget()

        card, body = self._card(self.body)
        card.pack(fill="x")

        _label(body, "安装位置", 9, "#b3becb", bg=CARD).pack(anchor="w", pady=(18, 0))
        row2 = tk.Frame(body, bg=CARD)
        row2.pack(fill="x", pady=(6, 0))
        row2.columnconfigure(0, weight=1)
        # 安装目录显示 basename + 完整路径走悬停提示：完整路径太长会截断成
        # "C:\Users\admin\AppData\L..."，用户看不出装到哪了。
        self._field_dir = _Field(row2, self.var_dir, placeholder="请选择安装目录",
                                 parent_bg=CARD, display_basename=True)
        self._field_dir.grid(row=0, column=0, sticky="ew")
        _Button(row2, "选择文件夹…", self._browse_dir, kind="secondary", width=88,
                height=36, parent_bg=CARD).grid(row=0, column=1, sticky="e",
                                                padx=(10, 0))

        _Check(body, "在桌面和「开始」菜单中创建快捷方式（可选）", self.var_shortcuts,
               parent_bg=CARD).pack(anchor="w", pady=(18, 0))

        # 给非技术用户一句「不用管这些」，降低面对路径框的困惑
        _label(body, "提示：保持默认即可，安装完成后会在桌面和「开始」菜单中创建快捷方式。", 9, "#8b96a3",
              bg=CARD).pack(anchor="w", pady=(12, 0))

        # 日志（安装时才展开，不占初始版面）
        self.log = _LogPane(self.body)
        self.log._owner = self      # 展开时能回调 _lock_size() 钉死窗口尺寸
        self.log.pack_forget()

        # 底部
        btns = tk.Frame(self.footer, bg=BG)
        btns.pack(side="right")
        self._btn_install = _Button(btns, "开始安装", self._start, kind="primary",
                                    width=126, height=42, parent_bg=BG)
        self._btn_install.pack(side="right")
        self._btn_exit = _Button(btns, "退出", self._close_now, kind="ghost",
                                 width=86, height=42, parent_bg=BG)
        self._btn_exit.pack(side="right", padx=(0, 10))

        self.status.configure(text="准备就绪")

    def _browse_payload(self):
        p = filedialog.askopenfilename(
            title="选择安装包",
            filetypes=[("安装包", "*.zip"), ("所有文件", "*.*")])
        if p:
            self.var_payload.set(p)
            self._field_payload._draw()   # 刷新显示的文件名
            self._mark_payload_ok()

    def _browse_dir(self):
        d = filedialog.askdirectory(title="选择安装目录",
                                    initialdir=self.var_dir.get())
        if d:
            self.var_dir.set(d)

    def _auto_find_payload(self):
        """自动定位安装包。

        装包时 setup.exe 与 payload zip 在同一目录 → 直接找到。
        找到就**不显示「安装包」那一行**（用户什么都不用做）；
        找不到才显示该行作为兜底，并给出明确提示。
        """
        if self.var_payload.get():
            self._mark_payload_ok()
            return
        from .payload import find_payload
        try:
            path = find_payload()
        except FileNotFoundError:
            self._show_payload_row(
                "没找到安装包。请把安装包（mio-taskhub-win64.zip）放到本程序"
                "同一目录下，或点「选择文件…」手动指定。")
            return
        self.var_payload.set(str(path))
        self._mark_payload_ok()

    def _show_payload_row(self, message: str) -> None:
        """显示「安装包」兜底行（仅自动定位失败时）。

        不用 `pack(before=...)` 定位 —— `winfo_children()[-1]` 可能是被
        `pack_forget()` 掉的控件（如日志面板），Tcl 会报
        `window "..." isn't packed`。槽位用 pack 顺序定位，稳。
        """
        try:
            self._payload_slot.pack(fill="x", pady=(0, 12))
        except Exception:  # noqa: BLE001
            pass
        self.status.configure(text="等待选择安装包")
        self.notice.show("err", "需要安装包", message)

    def _mark_payload_ok(self) -> None:
        """已找到安装包：隐藏兜底行 + 状态栏肯定反馈。"""
        try:
            self._payload_slot.pack_forget()
        except Exception:  # noqa: BLE001
            pass
        self.status.configure(text="已找到安装包，随时可以开始安装")

    def _start(self):
        if self._done:
            self._close_now()
            return
        # **必须在主线程读 Tk 变量**：从工作线程调 `var.get()` 会抛
        # `RuntimeError: main thread is not in main loop`（Tk 的变量存取
        # 不是线程安全的）。所以先把值取出来，再把**纯 Python 值**传给
        # 工作线程 —— 工作线程里一个 Tk 对象都不碰。
        try:
            payload_zip = self.var_payload.get()
            install_dir = self.var_dir.get()
            create_shortcuts = bool(self.var_shortcuts.get())
        except Exception as e:  # noqa: BLE001
            self.notice.show("err", "读取界面参数失败", repr(e))
            return
        if not payload_zip:
            self.notice.show("err", "还没有安装包",
                             "请先点「选择文件…」指定安装包")
            return
        self._done = True
        self._btn_install.set_enabled(False)
        self._btn_install.set_text("安装中…")
        self.status.configure(text="正在安装，请勿关闭窗口")
        self.notice.show("info", "正在安装", "正在解压并写入程序文件…")
        threading.Thread(target=self._work, args=(payload_zip, install_dir,
                                                  create_shortcuts),
                         daemon=True).start()

    def _work(self, payload_zip, install_dir, create_shortcuts):
        """工作线程：**只碰纯 Python 值**，不碰任何 Tk 对象。

        结果通过 `_ui_call` 回主线程刷新界面。
        """
        try:
            res = install(
                payload_zip=payload_zip or None,
                install_dir=install_dir or None,
                create_shortcuts=create_shortcuts,
            )
            for s in res.steps:
                self.post(self.log.append,
                          "[%s] %s%s" % ("OK" if s["ok"] else "!!", s["name"],
                                          (" — " + s["detail"]) if s["detail"] else ""))
            self.post(self._finish, res)
        except Exception as e:  # noqa: BLE001
            self.post(self.log.append, "安装异常：%r" % e)
            self.post(self._failed, str(e))

    def _finish(self, res):
        if res.ok:
            self.status.configure(text="安装完成")
            self.notice.show("ok", "安装完成",
                             "双击桌面或开始菜单的「%s」启动" % APP_DISPLAY_NAME)
            self._btn_install.set_text("完成")
            self._btn_install.set_enabled(True)
            self._close_later(1400)
        else:
            self._failed(res.message)

    def _failed(self, msg):
        self.status.configure(text="安装失败")
        self.notice.show("err", "安装失败", msg)
        self._btn_install.set_text("重试")
        self._btn_install.set_enabled(True)
        self._done = False


class UninstallerApp(_Base):
    """卸载向导。"""

    def __init__(self, root: tk.Tk):
        super().__init__(root, "%s 卸载程序" % APP_DISPLAY_NAME,
                         "移除程序文件、快捷方式与「应用和功能」注册项")
        self.var_dir = tk.StringVar(value=str(detect_install_dir()))
        self.var_purge = tk.BooleanVar(value=False)
        self._build()

    def _build(self):
        card, body = self._card(self.body)
        card.pack(fill="x")

        _label(body, "安装目录", 9, "#a8b3c0", bg=CARD).pack(anchor="w")
        row = tk.Frame(body, bg=CARD)
        row.pack(fill="x", pady=(6, 0))
        row.columnconfigure(0, weight=1)
        _Field(row, self.var_dir, placeholder="安装目录",
               parent_bg=CARD, display_basename=True).grid(row=0, column=0, sticky="ew")

        self._check_purge = _Check(body, "同时删除用户数据（数据库 / 日志 / 备份）",
                                  self.var_purge, parent_bg=CARD)
        self._check_purge.pack(anchor="w", pady=(18, 0))

        # 不可恢复操作的警告：默认**琥珀色**（潜在风险），
        # **勾选后变红**（即将执行）—— 不可恢复的操作要显眼。
        self._purge_warn = _label(body, "⚠ 勾选后将永久删除上述用户数据，无法恢复",
                                  9, "#e3b341", bg=CARD)
        self._purge_warn.pack(anchor="w", pady=(6, 0))
        self.var_purge.trace_add("write", self._on_purge_change)

        self.log = _LogPane(self.body)
        self.log._owner = self      # 展开时能回调 _lock_size() 钉死窗口尺寸
        self.log.pack_forget()

        btns = tk.Frame(self.footer, bg=BG)
        btns.pack(side="right")
        self._btn_uninstall = _Button(btns, "卸载", self._start, kind="danger",
                                      width=126, height=42, parent_bg=BG)
        self._btn_uninstall.pack(side="right")
        _Button(btns, "取消", self._close_now, kind="ghost", width=86, height=42,
                parent_bg=BG).pack(side="right", padx=(0, 10))

        self.status.configure(text="准备就绪（将保留用户数据）")

    def _on_purge_change(self, *_args) -> None:
        """勾选「删除用户数据」时强化警告 + 改按钮文案（不可恢复操作要显眼）。"""
        on = bool(self.var_purge.get())
        self._purge_warn.configure(fg=DANGER if on else "#e3b341")
        self._btn_uninstall.set_text("卸载并删除数据" if on else "卸载")
        self.status.configure(text="准备就绪（将删除用户数据）" if on
                              else "准备就绪（将保留用户数据）")

    def _start(self):
        # **必须在主线程读 Tk 变量**：从工作线程调 `var.get()` 会抛
        # `RuntimeError: main thread is not in main loop`（Tk 的变量存取
        # 不是线程安全的）。先取成纯 Python 值再交给工作线程。
        try:
            install_dir = self.var_dir.get()
            remove_data = bool(self.var_purge.get())
        except Exception as e:  # noqa: BLE001
            self.notice.show("err", "读取界面参数失败", repr(e))
            return
        self._btn_uninstall.set_enabled(False)
        self._btn_uninstall.set_text("卸载中…")
        self.status.configure(text="正在卸载，请勿关闭窗口")
        self.notice.show("info", "正在卸载", "正在停止程序并移除文件…")
        threading.Thread(target=self._work, args=(install_dir, remove_data),
                         daemon=True).start()

    def _work(self, install_dir, remove_data):
        """工作线程：**只碰纯 Python 值**，不碰任何 Tk 对象。"""
        try:
            res = uninstall(install_dir=install_dir or None,
                            remove_data=remove_data)
            for s in res.steps:
                self.post(self.log.append,
                          "[%s] %s%s" % ("OK" if s["ok"] else "!!", s["name"],
                                          (" — " + s["detail"]) if s["detail"] else ""))
            self.post(self._finish, res)
        except Exception as e:  # noqa: BLE001
            self.post(self.log.append, "卸载异常：%r" % e)
            self.post(self._failed, str(e))

    def _finish(self, res):
        if res.ok:
            self.status.configure(text="卸载完成")
            self.notice.show("ok", "卸载完成", "用户数据仍保留在 %s" % data_dir())
            self._btn_uninstall.set_text("完成")
            self._btn_uninstall.set_enabled(True)
            self._close_later(1200)
        else:
            self._failed(res.message)

    def _failed(self, msg):
        self.status.configure(text="卸载失败")
        self.notice.show("err", "卸载失败", msg)
        self._btn_uninstall.set_text("重试")
        self._btn_uninstall.set_enabled(True)


def run_installer(payload_zip=None) -> int:
    root = tk.Tk()
    InstallerApp(root, payload_zip=payload_zip)
    root.mainloop()
    return 0


def run_uninstaller() -> int:
    root = tk.Tk()
    UninstallerApp(root)
    root.mainloop()
    return 0
