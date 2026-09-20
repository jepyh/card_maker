"""元素属性编辑面板。"""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Any, Callable, Dict, List, Optional

from .. import fonts as fontlib
from ..model import Element, ImageElement, TextElement, fields_for
from .widgets import ColorInput, ScrollableFrame, Tooltip

ALIGN_BUTTONS = [
    ("水平居中", "hcenter"), ("垂直居中", "vcenter"),
    ("贴左", "left"), ("贴右", "right"), ("贴上", "top"), ("贴下", "bottom"),
]


class PropertyPanel(ttk.Frame):
    def __init__(self, master, on_change: Callable[[], None],
                 on_align: Callable[[str], None],
                 on_preview_row: Callable[[], Dict[str, Any]]):
        super().__init__(master)
        self.on_change = on_change
        self.on_align = on_align
        self.on_preview_row = on_preview_row
        self.element: Optional[Element] = None
        self._suspend = False
        self._vars: Dict[str, tk.Variable] = {}
        self._color_inputs: Dict[str, ColorInput] = {}

        header = ttk.Frame(self, padding=(8, 8, 8, 4))
        header.pack(fill="x")
        self.title = ttk.Label(header, text="未选中元素", font=("", 10, "bold"))
        self.title.pack(anchor="w")
        self.subtitle = ttk.Label(header, text="在左侧元素列表或画布中点选一个元素",
                                  foreground="#888888", wraplength=280, justify="left")
        self.subtitle.pack(anchor="w", pady=(2, 0))

        self.scroll = ScrollableFrame(self)
        self.scroll.pack(fill="both", expand=True)
        self.body = self.scroll.body

    # ------------------------------------------------------------------
    def set_element(self, el: Optional[Element]) -> None:
        self.element = el
        for child in self.body.winfo_children():
            child.destroy()
        self._vars.clear()
        self._color_inputs.clear()
        if el is None:
            self.title.configure(text="未选中元素")
            self.subtitle.configure(text="在左侧元素列表或画布中点选一个元素")
            return
        kind = "文本元素" if isinstance(el, TextElement) else "图片元素"
        self.title.configure(text=f"{el.name}")
        self.subtitle.configure(text=f"{kind}　·　可绑定 Excel 的「{el.name}」列")
        self._suspend = True
        try:
            self._build(el)
        finally:
            self._suspend = False

    # ------------------------------------------------------------------
    def _build(self, el: Element) -> None:
        self._build_align_row()
        fields = fields_for(el.type)
        groups: Dict[str, List[Dict[str, Any]]] = {
            "基本": [], "位置与尺寸": [], "字体与样式": [], "效果": [], "其他": [],
        }
        for f in fields:
            k = f["key"]
            if k in ("name", "content", "source", "x", "y", "width", "height",
                     "font_family", "font_size", "bold", "italic", "color",
                     "align", "valign", "line_spacing", "letter_spacing", "wrap",
                     "fit", "zoom", "corner_radius", "border_width", "border_color"):
                pass
            groups[self._group_of(k)].append(f)

        for gname in ("基本", "位置与尺寸", "字体与样式", "效果", "其他"):
            items = groups[gname]
            if not items:
                continue
            box = ttk.LabelFrame(self.body, text=gname, padding=(8, 6))
            box.pack(fill="x", padx=8, pady=(6, 2))
            box.columnconfigure(1, weight=1)
            for f in items:
                self._add_field(box, el, f)

    @staticmethod
    def _group_of(key: str) -> str:
        if key in ("name", "content", "source", "placeholder_text"):
            return "基本"
        if key in ("x", "y", "width", "height", "rotation", "zoom", "offset_x",
                   "offset_y", "var"):
            return "位置与尺寸"
        if key in ("font_family", "font_size", "bold", "italic", "color", "align",
                   "valign", "line_spacing", "letter_spacing", "wrap", "max_lines",
                   "ellipsis", "auto_shrink", "min_font_size", "text_transform",
                   "vertical"):
            return "字体与样式"
        if key in ("stroke_width", "stroke_color", "shadow", "shadow_offset",
                   "shadow_color", "bg_color", "bg_padding", "bg_radius",
                   "corner_radius", "border_width", "border_color", "opacity",
                   "grayscale"):
            return "效果"
        return "其他"

    def _build_align_row(self) -> None:
        box = ttk.LabelFrame(self.body, text="对齐到卡面", padding=(8, 6))
        box.pack(fill="x", padx=8, pady=(6, 2))
        grid = ttk.Frame(box)
        grid.pack(fill="x")
        for i, (label, action) in enumerate(ALIGN_BUTTONS):
            b = ttk.Button(grid, text=label, width=9,
                           command=lambda a=action: self.on_align(a))
            b.grid(row=i // 3, column=i % 3, padx=2, pady=2, sticky="ew")
        for c in range(3):
            grid.columnconfigure(c, weight=1)

    # ------------------------------------------------------------------
    def _add_field(self, parent: ttk.Frame, el: Element, f: Dict[str, Any]) -> None:
        key = f["key"]
        ftype = f["type"]
        label = f["label"]
        row = len(parent.grid_slaves(column=0))
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="nw",
                                           padx=(0, 6), pady=3)

        def changed(*_a, k=key, t=ftype):
            if self._suspend:
                return
            self._commit(k, t)

        if ftype == "bool":
            var = tk.BooleanVar(value=bool(getattr(el, key)))
            self._vars[key] = var
            cb = ttk.Checkbutton(parent, variable=var, command=changed,
                                 text="开启" if var.get() else "关闭")
            cb.grid(row=row, column=1, sticky="w", pady=3)
            var.trace_add("write", lambda *a, c=cb, v=var: c.configure(
                text="开启" if v.get() else "关闭"))
        elif ftype == "choice":
            var = tk.StringVar(value=str(getattr(el, key)))
            self._vars[key] = var
            cb = ttk.Combobox(parent, textvariable=var, values=f.get("choices", []),
                              state="readonly", width=14)
            cb.grid(row=row, column=1, sticky="ew", pady=3)
            cb.bind("<<ComboboxSelected>>", changed)
        elif ftype == "font":
            var = tk.StringVar(value=str(getattr(el, "font_family")))
            self._vars["font_family"] = var
            holder = ttk.Frame(parent)
            holder.grid(row=row, column=1, sticky="ew", pady=3)
            cb = ttk.Combobox(holder, textvariable=var, values=fontlib.family_choices(),
                              width=12)
            cb.pack(side="left", fill="x", expand=True)
            cb.bind("<<ComboboxSelected>>", changed)
            cb.bind("<Return>", changed)
            ttk.Button(holder, text="文件…", width=6,
                       command=lambda: self._pick_font_file(el)).pack(side="left", padx=2)
            ttk.Button(holder, text="×", width=3,
                       command=lambda: self._clear_font_file(el)).pack(side="left")
            path = getattr(el, "font_path", "")
            lbl = ttk.Label(parent, text=(os.path.basename(path) if path else "使用系统字体"),
                            foreground="#888888", wraplength=200, justify="left")
            lbl.grid(row=row, column=2, sticky="w", padx=4)
            if path:
                Tooltip(lbl, path)
        elif ftype in ("color", "color_opt"):
            ci = ColorInput(parent, value=str(getattr(el, key) or ""),
                            allow_empty=(ftype == "color_opt"),
                            on_change=lambda *a, k=key: self._commit(k, "str"))
            ci.grid(row=row, column=1, sticky="ew", pady=3)
            self._color_inputs[key] = ci
        elif ftype == "multiline":
            var = tk.StringVar(value=str(getattr(el, key) or ""))
            self._vars[key] = var
            txt = tk.Text(parent, height=4, width=24, wrap="word", relief="solid", bd=1,
                          font=("Consolas", 9))
            txt.insert("1.0", str(getattr(el, key) or ""))
            txt.grid(row=row, column=1, columnspan=2, sticky="ew", pady=3)
            txt.bind("<KeyRelease>", lambda e, k=key, w=txt: self._commit_text(k, w))
            hint = ttk.Label(parent, text="支持 {{列名}} 占位符", foreground="#888888")
            hint.grid(row=row + 1, column=1, columnspan=2, sticky="w")
        else:
            var = tk.StringVar(value=self._fmt(getattr(el, key)))
            self._vars[key] = var
            e = ttk.Entry(parent, textvariable=var, width=14)
            e.grid(row=row, column=1, sticky="ew", pady=3)
            var.trace_add("write", changed)

    @staticmethod
    def _fmt(v: Any) -> str:
        if isinstance(v, float):
            return ("%g" % v)
        return "" if v is None else str(v)

    # ------------------------------------------------------------------
    def _commit_text(self, key: str, widget: tk.Text) -> None:
        if self._suspend or self.element is None:
            return
        value = widget.get("1.0", "end-1c")
        setattr(self.element, key, value)
        self.on_change()

    def _commit(self, key: str, ftype: str) -> None:
        el = self.element
        if el is None:
            return
        if key in self._color_inputs:
            value = self._color_inputs[key].get()
            setattr(el, key, value)
            self.on_change()
            return
        var = self._vars.get(key)
        if var is None:
            return
        raw = var.get()
        if ftype == "bool":
            setattr(el, key, bool(raw))
        elif ftype == "choice":
            setattr(el, key, str(raw))
        elif ftype == "str" and key in ("color", "border_color", "stroke_color",
                                        "shadow_color", "bg_color"):
            setattr(el, key, str(raw))
        else:
            setattr(el, key, self._parse(raw, key, getattr(el, key)))
        self.on_change()

    @staticmethod
    def _parse(raw: str, key: str, old: Any) -> Any:
        s = str(raw).strip()
        if isinstance(old, bool):
            return s.strip().lower() in ("1", "true", "yes", "是", "开")
        if isinstance(old, int) and not isinstance(old, bool):
            try:
                return int(float(s))
            except ValueError:
                return old
        if isinstance(old, float):
            try:
                return float(s)
            except ValueError:
                return old
        if key in ("x", "y", "width", "height", "font_size", "line_spacing",
                   "letter_spacing", "rotation", "opacity", "corner_radius",
                   "border_width", "zoom", "offset_x", "offset_y", "stroke_width",
                   "shadow_offset", "bg_padding", "bg_radius", "min_font_size"):
            try:
                return float(s)
            except ValueError:
                return old
        if key == "max_lines":
            try:
                return int(float(s))
            except ValueError:
                return old
        return s

    # ------------------------------------------------------------------
    def _pick_font_file(self, el: Element) -> None:
        path = filedialog.askopenfilename(
            title="选择字体文件",
            filetypes=[("字体文件", "*.ttf *.ttc *.otf"), ("所有文件", "*.*")])
        if not path:
            return
        setattr(el, "font_path", path)
        setattr(el, "font_family", os.path.splitext(os.path.basename(path))[0])
        self.set_element(el)
        self.on_change()

    def _clear_font_file(self, el: Element) -> None:
        setattr(el, "font_path", "")
        self.set_element(el)
        self.on_change()

    def sync_from_element(self, el: Element) -> None:
        """外部（如拖拽）改了坐标后，把面板数值刷新一下。"""
        self._suspend = True
        try:
            for key, var in self._vars.items():
                if key in self._color_inputs or key == "font_family":
                    continue
                if not hasattr(el, key):
                    continue
                new = self._fmt(getattr(el, key))
                if str(var.get()) != new:
                    var.set(new)
            for key, ci in self._color_inputs.items():
                if hasattr(el, key):
                    cur = str(getattr(el, key) or "")
                    if ci.get() != cur:
                        ci.set(cur)
        finally:
            self._suspend = False
