"""界面通用组件。"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..model import normalize_color


class ScrollableFrame(ttk.Frame):
    """可纵向滚动的容器，内容放在 self.body。"""

    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.vsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", self._on_body)
        self.canvas.bind("<Configure>", self._on_canvas)
        self.canvas.bind("<Enter>", lambda e: self._bind_wheel(True))
        self.canvas.bind("<Leave>", lambda e: self._bind_wheel(False))

    def _on_body(self, _e=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, event):
        self.canvas.itemconfigure(self._win, width=event.width)

    def _bind_wheel(self, on: bool):
        if on:
            self.canvas.bind_all("<MouseWheel>", self._wheel)
        else:
            self.canvas.unbind_all("<MouseWheel>")

    def _wheel(self, event):
        self.canvas.yview_scroll(int(-event.delta / 120), "units")


class ColorInput(ttk.Frame):
    """颜色输入：文本框 + 预览块 + 取色按钮。allow_empty=True 时允许留空。"""

    PRESETS = [
        "#000000", "#FFFFFF", "#333333", "#666666", "#999999", "#CCCCCC",
        "#C0392B", "#E74C3C", "#E67E22", "#F1C40F", "#27AE60", "#16A085",
        "#2980B9", "#2C3E50", "#8E44AD", "#D35400", "#7F8C8D", "#C9A227",
    ]

    def __init__(self, master, value: str = "#000000", allow_empty: bool = False,
                 on_change: Optional[Callable[[str], None]] = None, width: int = 10):
        super().__init__(master)
        self.allow_empty = allow_empty
        self.on_change = on_change
        self.var = tk.StringVar(value=value or "")
        self.swatch = tk.Label(self, width=3, relief="solid", bd=1, bg=self._bg_for(value))
        self.swatch.pack(side="left", padx=(0, 4))
        self.entry = ttk.Entry(self, textvariable=self.var, width=width)
        self.entry.pack(side="left")
        self.btn = ttk.Button(self, text="▾", width=3, command=self._popup)
        self.btn.pack(side="left", padx=(2, 0))
        self.var.trace_add("write", lambda *a: self._on_typed())

    def _bg_for(self, value: str) -> str:
        v = (value or "").strip()
        if not v:
            return "#F0F0F0"
        try:
            c = normalize_color(v, "#F0F0F0").lstrip("#")
            if len(c) == 8:  # Tk 不认带透明度的色值，显示时去掉 alpha
                c = c[:6]
            if len(c) == 4:
                c = c[:3]
            return "#" + c
        except Exception:
            return "#F0F0F0"

    def _on_typed(self):
        self.swatch.configure(bg=self._bg_for(self.var.get()))
        if self.on_change:
            self.on_change(self.var.get().strip())

    def set(self, value: str):
        self.var.set(value or "")
        self.swatch.configure(bg=self._bg_for(value))

    def get(self) -> str:
        return self.var.get().strip()

    def _popup(self):
        top = tk.Toplevel(self)
        top.title("选择颜色")
        top.transient(self.winfo_toplevel())
        top.resizable(False, False)
        body = ttk.Frame(top, padding=8)
        body.pack(fill="both", expand=True)
        grid = ttk.Frame(body)
        grid.pack()
        for i, c in enumerate(self.PRESETS):
            lb = tk.Label(grid, bg=c, width=3, height=1, relief="ridge", bd=2)
            lb.grid(row=i // 6, column=i % 6, padx=2, pady=2)
            lb.bind("<Button-1>", lambda e, col=c: (self.set(col), top.destroy()))
        row = ttk.Frame(body)
        row.pack(fill="x", pady=(8, 0))
        ttk.Label(row, text="自定义:").pack(side="left")
        e = ttk.Entry(row, width=12)
        e.insert(0, self.get() or "#000000")
        e.pack(side="left", padx=4)

        def ok():
            self.set(e.get().strip())
            top.destroy()

        ttk.Button(row, text="确定", command=ok).pack(side="left")
        if self.allow_empty:
            def clear():
                self.set("")
                top.destroy()
            ttk.Button(row, text="清空", command=clear).pack(side="left", padx=4)
        e.bind("<Return>", lambda ev: ok())
        e.focus_set()


class ProgressDialog(tk.Toplevel):
    """带进度条与取消按钮的模态对话框。"""

    def __init__(self, master, title: str = "处理中", maximum: int = 100):
        super().__init__(master)
        self.title(title)
        self.transient(master)
        self.resizable(False, False)
        self.cancelled = False
        self._closed = False
        body = ttk.Frame(self, padding=16)
        body.pack(fill="both", expand=True)
        self.label = ttk.Label(body, text="准备中…", width=48)
        self.label.pack(anchor="w")
        self.bar = ttk.Progressbar(body, length=380, mode="determinate", maximum=max(1, maximum))
        self.bar.pack(pady=10)
        self.detail = ttk.Label(body, text="", foreground="#888888", width=48)
        self.detail.pack(anchor="w")
        self.btn = ttk.Button(body, text="取消", command=self._cancel)
        self.btn.pack(pady=(10, 0))
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.update_idletasks()
        try:
            px = master.winfo_rootx() + (master.winfo_width() - self.winfo_width()) // 2
            py = master.winfo_rooty() + (master.winfo_height() - self.winfo_height()) // 3
            self.geometry(f"+{max(0, px)}+{max(0, py)}")
        except Exception:
            pass
        self.grab_set()

    def _cancel(self):
        self.cancelled = True
        self.detail.configure(text="正在取消…")
        self.btn.configure(state="disabled")

    def step(self, done: int, total: int, message: str = "") -> bool:
        if not self._closed:
            self.bar.configure(maximum=max(1, total))
            self.bar.configure(value=done)
            if message:
                self.label.configure(text=message)
            self.detail.configure(text=f"{done} / {total}")
            try:
                self.update()
            except tk.TclError:
                pass
        return not self.cancelled

    def close(self):
        self._closed = True
        try:
            self.grab_release()
        except tk.TclError:
            pass
        try:
            self.destroy()
        except tk.TclError:
            pass


class Tooltip:
    """鼠标悬停提示。"""

    def __init__(self, widget: tk.Widget, text: str):
        self.widget = widget
        self.text = text
        self.tip: Optional[tk.Toplevel] = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _show(self, _e=None):
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 18
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self.tip, text=self.text, bg="#FFFFE0", relief="solid", bd=1,
                 justify="left", padx=6, pady=3).pack()

    def _hide(self, _e=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None


def labeled_entry(parent, label: str, var: tk.Variable, width: int = 10,
                  row: int = 0, col: int = 0, tip: str = "") -> ttk.Entry:
    ttk.Label(parent, text=label).grid(row=row, column=col, sticky="w", padx=(0, 4), pady=2)
    e = ttk.Entry(parent, textvariable=var, width=width)
    e.grid(row=row, column=col + 1, sticky="ew", pady=2)
    if tip:
        Tooltip(e, tip)
    return e
