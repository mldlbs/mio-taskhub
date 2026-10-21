# -*- coding: utf-8 -*-
"""安装向导 / 卸载向导的 GUI（Tkinter）。

设计原则：
- **不依赖网络**：安装包在本地 zip 里，安装过程纯本地操作。
- **可静默**：`--silent` / `/S` 走 CLI 路径（CI、批量部署用）。
- **失败要说人话**：每一步的结果都显示在界面上，失败时给出原因与建议。
"""
from __future__ import annotations

import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .install import detect_install_dir, install, uninstall
from .paths import (
    APP_DISPLAY_NAME,
    app_version,
    data_dir,
    default_install_dir,
)

BG = "#12161f"
FG = "#e6edf3"
MUTED = "#8b949e"
ACCENT = "#3ddc97"
CARD = "#1b2029"
DANGER = "#f85149"


class _Theme:
    """把 ttk 主题染成与 Web UI 一致的深色。"""

    @staticmethod
    def apply(root: tk.Tk) -> None:
        try:
            style = ttk.Style(root)
            style.theme_use("clam")
        except tk.TclError:
            return
        style.configure(".", background=BG, foreground=FG,
                        fieldbackground=CARD, borderwidth=0, focuscolor=ACCENT)
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED)
        style.configure("Title.TLabel", background=BG, foreground=FG,
                        font=("Microsoft YaHei UI", 16, "bold"))
        style.configure("Sub.TLabel", background=BG, foreground=MUTED,
                        font=("Microsoft YaHei UI", 9))
        style.configure("TButton", background=CARD, foreground=FG,
                        padding=(14, 7), relief="flat", borderwidth=0)
        style.map("TButton",
                  background=[("active", "#232936"), ("pressed", "#10141c")])
        style.configure("Accent.TButton", background=ACCENT, foreground="#06121b",
                        font=("Microsoft YaHei UI", 10, "bold"), padding=(18, 9))
        style.map("Accent.TButton",
                  background=[("active", "#4fe8a8"), ("pressed", "#2bbd7f")])
        style.configure("Danger.TButton", background=DANGER, foreground="#ffffff",
                        font=("Microsoft YaHei UI", 10, "bold"), padding=(18, 9))
        style.map("Danger.TButton",
                  background=[("active", "#ff6b63"), ("pressed", "#d63f38")])
        style.configure("TCheckbutton", background=BG, foreground=FG)
        style.configure("TProgressbar", background=CARD, troughcolor="#0d1017",
                        borderwidth=0)
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD)


class InstallerApp:
    """安装向导。"""

    def __init__(self, root: tk.Tk, payload_zip=None):
        self.root = root
        self.payload_zip = payload_zip
        self.var_payload = tk.StringVar(value=payload_zip or "")
        self.var_dir = tk.StringVar(value=str(default_install_dir()))
        self.var_shortcuts = tk.BooleanVar(value=True)
        self.var_status = tk.StringVar(value="准备就绪")
        self._done = False

        root.title("%s 安装程序" % APP_DISPLAY_NAME)
        root.configure(bg=BG)
        root.resizable(False, False)
        _Theme.apply(root)
        self._build()
        self.root.after(60, self._auto_find_payload)

    def _build(self) -> None:
        pad = 18
        tk.Label(self.root, text="%s 安装程序" % APP_DISPLAY_NAME,
                 bg=BG, fg=FG, font=("Microsoft YaHei UI", 16, "bold")).pack(
            anchor="w", padx=pad, pady=(pad, 0))
        tk.Label(self.root, text="版本 %s · 免 Python · 本机安装" % app_version(),
                 bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(
            anchor="w", padx=pad, pady=(2, 14))

        body = tk.Frame(self.root, bg=CARD)
        body.pack(fill="x", padx=pad, pady=(0, 12))
        inner = tk.Frame(body, bg=CARD)
        inner.pack(fill="x", padx=14, pady=12)

        self._label(inner, "安装包", 0)
        self._entry_row(inner, self.var_payload, self._browse_payload, 0)

        self._label(inner, "安装目录", 2)
        self._entry_row(inner, self.var_dir, self._browse_dir, 2)

        tk.Checkbutton(inner, text="创建开始菜单和桌面快捷方式",
                       variable=self.var_shortcuts, bg=CARD, fg=FG,
                       activebackground=CARD, activeforeground=FG,
                       selectcolor=CARD, anchor="w").grid(row=4, column=0, columnspan=3,
                                                        sticky="w", pady=(10, 0))

        self.progress = ttk.Progressbar(self.root, mode="indeterminate", length=440)
        self.progress.pack(fill="x", padx=pad, pady=(0, 6))
        self.log = tk.Text(self.root, height=9, width=58, bg="#0d1017", fg=FG,
                           insertbackground=FG, relief="flat", bd=0,
                           font=("Consolas", 9), state="disabled")
        self.log.pack(fill="x", padx=pad, pady=(0, 10))

        self.lbl_status = tk.Label(self.root, textvariable=self.var_status,
                                   bg=BG, fg=MUTED, anchor="w",
                                   font=("Microsoft YaHei UI", 9))
        self.lbl_status.pack(fill="x", padx=pad, pady=(0, 8))

        btns = tk.Frame(self.root, bg=BG)
        btns.pack(fill="x", padx=pad, pady=(0, pad))
        ttk.Button(btns, text="安装", style="Accent.TButton",
                   command=self._start).pack(side="right")
        ttk.Button(btns, text="退出", command=self.root.destroy).pack(side="right", padx=8)

    def _label(self, parent, text, row):
        tk.Label(parent, text=text, bg=CARD, fg=MUTED,
                 font=("Microsoft YaHei UI", 9)).grid(row=row, column=0, sticky="w",
                                                     pady=(6, 0))

    def _entry_row(self, parent, var, browse_cmd, row):
        e = tk.Entry(parent, textvariable=var, bg="#0d1017", fg=FG,
                     insertbackground=FG, relief="flat", bd=0, width=44)
        e.grid(row=row + 1, column=0, sticky="ew", padx=(0, 8), pady=(3, 0))
        ttk.Button(parent, text="浏览…", command=browse_cmd).grid(
            row=row + 1, column=1, sticky="w")
        parent.columnconfigure(0, weight=1)

    def _browse_payload(self):
        p = filedialog.askopenfilename(
            title="选择安装包",
            filetypes=[("安装包", "*.zip"), ("所有文件", "*.*")])
        if p:
            self.var_payload.set(p)

    def _browse_dir(self):
        d = filedialog.askdirectory(title="选择安装目录", initialdir=self.var_dir.get())
        if d:
            self.var_dir.set(d)

    def _auto_find_payload(self):
        if self.var_payload.get():
            return
        from .payload import find_payload
        try:
            self.var_payload.set(str(find_payload()))
        except FileNotFoundError:
            self._append("未自动找到安装包，请手动选择 %s" % "mio-taskhub-win64.zip")

    def _append(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _start(self):
        if self._done:
            self.root.destroy()
            return
        self._done = True
        self.progress.start(12)
        self.var_status.set("正在安装，请勿关闭窗口…")
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self):
        try:
            res = install(
                payload_zip=self.var_payload.get() or None,
                install_dir=self.var_dir.get() or None,
                create_shortcuts=bool(self.var_shortcuts.get()),
            )
            for s in res.steps:
                self.root.after(0, self._append,
                                "[%s] %s%s" % ("OK" if s["ok"] else "!!", s["name"],
                                                (" — " + s["detail"]) if s["detail"] else ""))
            self.root.after(0, self._finish, res)
        except Exception as e:  # noqa: BLE001
            self.root.after(0, self._append, "安装异常：%r" % e)
            self.root.after(0, self._failed, str(e))

    def _finish(self, res):
        self.progress.stop()
        if res.ok:
            self.var_status.set("安装完成 ✅")
            messagebox.showinfo(
                "安装完成",
                "已安装到：\n%s\n\n下一步：\n"
                "1. 双击桌面或开始菜单的「%s」启动\n"
                "2. 双击安装目录里的 setup-agent.bat 让 AI 助手接管任务"
                % (res.install_dir, APP_DISPLAY_NAME))
            self.root.after(1500, self.root.destroy)
        else:
            self._failed(res.message)

    def _failed(self, msg):
        self.progress.stop()
        self.var_status.set("安装失败")
        messagebox.showerror("安装失败", "%s\n\n可查看下方日志。" % msg)


class UninstallerApp:
    """卸载向导。"""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.var_dir = tk.StringVar(value=str(detect_install_dir()))
        self.var_purge = tk.BooleanVar(value=False)
        self.var_status = tk.StringVar(value="")

        root.title("%s 卸载程序" % APP_DISPLAY_NAME)
        root.configure(bg=BG)
        root.resizable(False, False)
        _Theme.apply(root)
        self._build()

    def _build(self) -> None:
        pad = 18
        tk.Label(self.root, text="卸载 %s" % APP_DISPLAY_NAME,
                 bg=BG, fg=FG, font=("Microsoft YaHei UI", 16, "bold")).pack(
            anchor="w", padx=pad, pady=(pad, 0))
        tk.Label(self.root, text="移除程序文件、快捷方式与「应用和功能」注册项",
                 bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(
            anchor="w", padx=pad, pady=(2, 14))

        body = tk.Frame(self.root, bg=CARD)
        body.pack(fill="x", padx=pad, pady=(0, 12))
        inner = tk.Frame(body, bg=CARD)
        inner.pack(fill="x", padx=14, pady=12)

        tk.Label(inner, text="安装目录", bg=CARD, fg=MUTED,
                 font=("Microsoft YaHei UI", 9)).grid(row=0, column=0, sticky="w")
        tk.Entry(inner, textvariable=self.var_dir, bg="#0d1017", fg=FG,
                 insertbackground=FG, relief="flat", bd=0, width=44).grid(
            row=1, column=0, sticky="ew", pady=(3, 0))
        inner.columnconfigure(0, weight=1)

        tk.Checkbutton(inner, text="同时删除用户数据（数据库 / 日志 / 备份，不可恢复）",
                       variable=self.var_purge, bg=CARD, fg=FG,
                       activebackground=CARD, activeforeground=FG,
                       selectcolor=CARD, anchor="w").grid(row=2, column=0, sticky="w",
                                                        pady=(12, 0))

        self.log = tk.Text(self.root, height=8, width=58, bg="#0d1017", fg=FG,
                           insertbackground=FG, relief="flat", bd=0,
                           font=("Consolas", 9), state="disabled")
        self.log.pack(fill="x", padx=pad, pady=(0, 10))
        tk.Label(self.root, textvariable=self.var_status, bg=BG, fg=MUTED, anchor="w",
                 font=("Microsoft YaHei UI", 9)).pack(fill="x", padx=pad, pady=(0, 8))

        btns = tk.Frame(self.root, bg=BG)
        btns.pack(fill="x", padx=pad, pady=(0, pad))
        ttk.Button(btns, text="卸载", style="Danger.TButton",
                   command=self._start).pack(side="right")
        ttk.Button(btns, text="取消", command=self.root.destroy).pack(side="right", padx=8)

    def _append(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _start(self):
        self.var_status.set("正在卸载…")
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self):
        try:
            res = uninstall(install_dir=self.var_dir.get() or None,
                            remove_data=bool(self.var_purge.get()))
            for s in res.steps:
                self.root.after(0, self._append,
                                "[%s] %s%s" % ("OK" if s["ok"] else "!!", s["name"],
                                                (" — " + s["detail"]) if s["detail"] else ""))
            self.root.after(0, self._finish, res)
        except Exception as e:  # noqa: BLE001
            self.root.after(0, self._append, "卸载异常：%r" % e)
            self.root.after(0, self._failed, str(e))

    def _finish(self, res):
        if res.ok:
            self.var_status.set("卸载完成 ✅")
            messagebox.showinfo("卸载完成",
                                "程序已移除。\n\n用户数据仍保留在：\n%s" % data_dir())
            self.root.after(1200, self.root.destroy)
        else:
            self._failed(res.message)

    def _failed(self, msg):
        self.var_status.set("卸载失败")
        messagebox.showerror("卸载失败", "%s\n\n可查看下方日志。" % msg)


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
