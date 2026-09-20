"""卡片制作器主界面。"""
from __future__ import annotations

import copy
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable, Dict, List, Optional

from PIL import Image

from .. import samples
from ..data_source import (
    DataTable,
    apply_row,
    auto_fill_from_columns,
    base_columns,
    list_sheets,
    load_table,
    override_columns,
)
from ..exporter import build_name, export_cards, export_pdf
from ..model import (
    CARD_PRESETS,
    Element,
    ImageElement,
    TextElement,
    CardTemplate,
    default_template,
    from_px,
    to_px,
    UNIT_ALIASES,
)
from ..renderer import CardRenderer, render_string
from .canvas import CardCanvas
from .props import PropertyPanel
from .widgets import ColorInput, ProgressDialog, Tooltip

APP_TITLE = "卡片制作器"
PREVIEW_IMG_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif")


class ExportDialog(tk.Toplevel):
    def __init__(self, app: "CardMakerApp"):
        super().__init__(app)
        self.app = app
        self.title("批量导出")
        self.resizable(False, False)
        self.transient(app)
        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)

        ttk.Label(body, text="输出目录").grid(row=0, column=0, sticky="w", pady=3)
        self.out_var = tk.StringVar(value=os.path.join(os.path.abspath("output"), "cards"))
        ttk.Entry(body, textvariable=self.out_var, width=44).grid(row=0, column=1, sticky="ew")
        ttk.Button(body, text="浏览…", command=self._pick_dir).grid(row=0, column=2, padx=(6, 0))

        ttk.Label(body, text="图片格式").grid(row=1, column=0, sticky="w", pady=3)
        self.fmt_var = tk.StringVar(value="PNG")
        ttk.Combobox(body, textvariable=self.fmt_var, state="readonly", width=12,
                     values=["PNG", "JPG", "WEBP", "BMP", "PDF(多页)"]).grid(
            row=1, column=1, sticky="w")

        ttk.Label(body, text="文件名模板").grid(row=2, column=0, sticky="w", pady=3)
        self.name_var = tk.StringVar(value=self._default_pattern())
        ttk.Entry(body, textvariable=self.name_var, width=44).grid(row=2, column=1, sticky="ew")
        ttk.Label(body, foreground="#888888", justify="left", wraplength=330,
                  text="可用占位符：{index} 序号（支持 {index:03d} 补零）、{row} 行号、"
                       "{sheet} 工作表、{{列名}} 取该列的值").grid(row=3, column=1, sticky="w")

        ttk.Label(body, text="导出范围").grid(row=4, column=0, sticky="w", pady=3)
        rng = ttk.Frame(body)
        rng.grid(row=4, column=1, sticky="w")
        self.range_var = tk.StringVar(value="all")
        ttk.Radiobutton(rng, text="全部行", value="all", variable=self.range_var).pack(side="left")
        ttk.Radiobutton(rng, text="当前行", value="current",
                        variable=self.range_var).pack(side="left", padx=8)
        ttk.Radiobutton(rng, text="区间", value="range", variable=self.range_var).pack(side="left")
        self.from_var = tk.StringVar(value="1")
        self.to_var = tk.StringVar(value=str(max(1, app.table.row_count)))
        ttk.Entry(rng, textvariable=self.from_var, width=5).pack(side="left", padx=(4, 2))
        ttk.Label(rng, text="到").pack(side="left")
        ttk.Entry(rng, textvariable=self.to_var, width=5).pack(side="left", padx=2)

        ttk.Label(body, text="JPG/WEBP 质量").grid(row=5, column=0, sticky="w", pady=3)
        self.q_var = tk.StringVar(value="95")
        ttk.Spinbox(body, from_=10, to=100, textvariable=self.q_var, width=6).grid(
            row=5, column=1, sticky="w")

        btns = ttk.Frame(body)
        btns.grid(row=6, column=0, columnspan=3, pady=(14, 0))
        ttk.Button(btns, text="开始导出", command=self._start).pack(side="left", padx=4)
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="left", padx=4)
        self.bind("<Escape>", lambda e: self.destroy())
        self.grab_set()

    def _default_pattern(self) -> str:
        cols = [c for c in base_columns(self.app.table.headers) if c in self.app.table.rows[0]] \
            if self.app.table.row_count else []
        name_col = next((c for c in ("标题", "名称", "名字", "title", "name") if c in cols), None)
        return "{index:03d}_" + ("{%s}" % name_col if name_col else "")

    def _pick_dir(self):
        d = filedialog.askdirectory(parent=self, title="选择输出目录")
        if d:
            self.out_var.set(d)

    def _start(self):
        app = self.app
        if not app.table.row_count:
            messagebox.showwarning("批量导出", "请先导入 Excel 数据（左侧「数据」页签）", parent=self)
            return
        out_dir = self.out_var.get().strip()
        if not out_dir:
            messagebox.showwarning("批量导出", "请填写输出目录", parent=self)
            return
        fmt = self.fmt_var.get()
        try:
            quality = max(10, min(100, int(self.q_var.get())))
        except ValueError:
            quality = 95
        rng = self.range_var.get()
        if rng == "current":
            rows = [app.current_row_dict()]
        elif rng == "range":
            try:
                a = max(1, int(self.from_var.get()))
                b = max(1, int(self.to_var.get()))
            except ValueError:
                messagebox.showwarning("批量导出", "区间行号无效", parent=self)
                return
            lo, hi = min(a, b), max(a, b)
            rows = app.table.rows[lo - 1: hi]
        else:
            rows = app.table.rows
        sub = DataTable(path=app.table.path, sheet=app.table.sheet,
                        headers=app.table.headers, rows=rows)
        pattern = self.name_var.get().strip() or "{index}"
        self.destroy()

        def job(progress):
            if fmt.startswith("PDF"):
                target = os.path.join(out_dir, "卡片合集.pdf")
                return export_pdf(app.tpl, sub, target, renderer=app.canvas.renderer,
                                  progress=progress), [target]
            done, errors = export_cards(
                app.tpl, sub, out_dir, fmt=fmt.lower().replace("(多页)", ""),
                name_pattern=pattern, quality=quality, dpi=app.tpl.dpi,
                renderer=app.canvas.renderer, progress=progress)
            return (done, errors), []

        app.run_with_progress("批量导出", job, self._on_done)

    @staticmethod
    def _on_done(result):
        (done, errors), files = result
        msg = f"成功导出 {done} 张卡片。"
        if errors:
            msg += f"\n\n其中 {len(errors)} 行出错：\n" + "\n".join(errors[:5])
        messagebox.showinfo("批量导出", msg)


class CardMakerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        try:
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
            self.geometry("%dx%d" % (min(1380, sw - 40), min(860, sh - 120)))
        except Exception:
            self.geometry("1380x860")
        self.minsize(1024, 640)

        self.tpl: CardTemplate = default_template()
        self.table = DataTable()
        self.row_index = 0
        self.current_file: Optional[str] = None
        self.unit = "px"
        self._suspend = False

        self._build_style()
        self._build_menu()
        self._build_toolbar()
        self._build_body()
        self._build_statusbar()
        self._bind_keys()

        self.after(80, self._first_refresh)

    # ---------------------------------------------------------- UI 骨架
    def _build_style(self):
        style = ttk.Style(self)
        try:
            if "vista" in style.theme_names():
                style.theme_use("vista")
            elif "clam" in style.theme_names():
                style.theme_use("clam")
        except Exception:
            pass
        style.configure("TNotebook.Tab", padding=(14, 6))
        style.configure("Tool.TFrame", padding=2)

    def _build_menu(self):
        m = tk.Menu(self)
        fm = tk.Menu(m, tearoff=0)
        fm.add_command(label="新建模板", command=self.new_template, accelerator="Ctrl+N")
        fm.add_command(label="打开模板…", command=self.open_template, accelerator="Ctrl+O")
        fm.add_command(label="保存模板", command=self.save_template, accelerator="Ctrl+S")
        fm.add_separator()
        fm.add_command(label="导入 Excel…", command=self.import_excel, accelerator="Ctrl+I")
        fm.add_command(label="重新加载数据", command=self.reload_table)
        fm.add_separator()
        fm.add_command(label="退出", command=self.destroy)
        m.add_cascade(label="文件", menu=fm)

        em = tk.Menu(m, tearoff=0)
        em.add_command(label="添加文本元素", command=lambda: self.add_element("text"))
        em.add_command(label="添加图片元素", command=lambda: self.add_element("image"))
        em.add_separator()
        em.add_command(label="删除选中元素", command=self.delete_selected, accelerator="Delete")
        em.add_command(label="复制选中元素", command=self.duplicate_selected, accelerator="Ctrl+D")
        m.add_cascade(label="编辑", menu=em)

        vm = tk.Menu(m, tearoff=0)
        self._menu_boxes = tk.BooleanVar(value=True)
        vm.add_checkbutton(label="显示元素边框", variable=self._menu_boxes,
                           command=self._toggle_boxes)
        vm.add_command(label="放大", command=lambda: self.canvas.zoom_by(1.2))
        vm.add_command(label="缩小", command=lambda: self.canvas.zoom_by(1 / 1.2))
        vm.add_command(label="适应窗口", command=lambda: self.canvas.set_zoom_mode("fit"))
        m.add_cascade(label="视图", menu=vm)

        hm = tk.Menu(m, tearoff=0)
        hm.add_command(label="使用说明", command=self.show_help)
        hm.add_command(label="生成示例数据", command=self.make_samples)
        m.add_cascade(label="帮助", menu=hm)
        self.configure(menu=m)

    def _build_toolbar(self):
        bar = ttk.Frame(self, padding=(8, 6))
        bar.pack(fill="x")
        ttk.Button(bar, text="新建", command=self.new_template).pack(side="left")
        ttk.Button(bar, text="打开模板", command=self.open_template).pack(side="left", padx=4)
        ttk.Button(bar, text="保存模板", command=self.save_template).pack(side="left")
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(bar, text="导入 Excel", command=self.import_excel).pack(side="left")
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(bar, text="导出本张", command=self.export_current).pack(side="left")
        ttk.Button(bar, text="批量导出…", command=self.batch_export).pack(side="left", padx=4)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(bar, text="生成示例数据", command=self.make_samples).pack(side="left")
        self._boxes_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="显示元素框", variable=self._boxes_var,
                        command=self._toggle_boxes).pack(side="right")

    def _build_body(self):
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        self._build_left_panel(body)
        self._build_right_panel(body)
        self._build_center_panel(body)

    def _build_left_panel(self, body):
        left = ttk.Frame(body, width=352, padding=(4, 2, 2, 2))
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        nb = ttk.Notebook(left)
        nb.pack(fill="both", expand=True)
        self.tab_card = ttk.Frame(nb, padding=8)
        self.tab_elements = ttk.Frame(nb, padding=8)
        self.tab_data = ttk.Frame(nb, padding=8)
        nb.add(self.tab_card, text=" 卡片 ")
        nb.add(self.tab_elements, text=" 元素 ")
        nb.add(self.tab_data, text=" 数据 ")
        self._build_card_tab(self.tab_card)
        self._build_elements_tab(self.tab_elements)
        self._build_data_tab(self.tab_data)

    def _build_right_panel(self, body):
        right = ttk.Frame(body, width=372, padding=(2, 2, 4, 2))
        right.pack(side="right", fill="y")
        right.pack_propagate(False)
        self.props = PropertyPanel(right, self._on_prop_changed,
                                   self._align_element, self.current_row_dict)
        self.props.pack(fill="both", expand=True)

    def _build_center_panel(self, body):
        mid = ttk.Frame(body)
        mid.pack(side="left", fill="both", expand=True)
        self.canvas = CardCanvas(mid, self._tpl, self._preview_row,
                                 self._on_canvas_select, self._on_canvas_changed)
        self.canvas.pack(fill="both", expand=True)
        nav = ttk.Frame(mid, padding=(8, 4))
        nav.pack(fill="x")
        ttk.Button(nav, text="◀ 上一行", width=10, command=lambda: self.go_row(-1)).pack(side="left")
        self.row_var = tk.StringVar(value="0")
        self.row_spin = ttk.Spinbox(nav, from_=1, to=1, width=7, textvariable=self.row_var,
                                    command=self._on_row_spin)
        self.row_spin.pack(side="left", padx=6)
        self.row_total = ttk.Label(nav, text="/ 0 行")
        self.row_total.pack(side="left")
        ttk.Button(nav, text="下一行 ▶", width=10, command=lambda: self.go_row(1)).pack(side="left", padx=6)
        ttk.Separator(nav, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Label(nav, text="缩放").pack(side="left")
        ttk.Button(nav, text="－", width=3, command=lambda: self.canvas.zoom_by(1 / 1.2)).pack(side="left", padx=2)
        ttk.Button(nav, text="＋", width=3, command=lambda: self.canvas.zoom_by(1.2)).pack(side="left")
        ttk.Button(nav, text="适应窗口", command=lambda: self.canvas.set_zoom_mode("fit")).pack(side="left", padx=4)
        ttk.Button(nav, text="100%", command=lambda: self.canvas.set_zoom_mode(1.0)).pack(side="left")
        self.canvas_hint = ttk.Label(nav, text="拖拽移动元素 · 拖右下角柄改尺寸 · 方向键微调",
                                     foreground="#999999")
        self.canvas_hint.pack(side="right")

    def _build_statusbar(self):
        bar = ttk.Frame(self, padding=(8, 3))
        bar.pack(fill="x")
        self.status = ttk.Label(bar, text="就绪")
        self.status.pack(side="left")
        self.status_right = ttk.Label(bar, text="", foreground="#888888")
        self.status_right.pack(side="right")

    # ---------------------------------------------------------- 左侧页签
    def _build_card_tab(self, parent):
        box = ttk.LabelFrame(parent, text="卡片尺寸", padding=8)
        box.pack(fill="x")
        self.preset_var = tk.StringVar(value="")
        ttk.Label(box, text="预设").grid(row=0, column=0, sticky="w", pady=2)
        cb = ttk.Combobox(box, textvariable=self.preset_var, state="readonly", width=24,
                          values=[p["name"] for p in CARD_PRESETS])
        cb.grid(row=0, column=1, columnspan=2, sticky="ew", pady=2)
        cb.bind("<<ComboboxSelected>>", self._on_preset)

        ttk.Label(box, text="单位").grid(row=1, column=0, sticky="w", pady=2)
        self.unit_var = tk.StringVar(value="px")
        unit_cb = ttk.Combobox(box, textvariable=self.unit_var, state="readonly", width=8,
                               values=["px", "mm", "cm", "in"])
        unit_cb.grid(row=1, column=1, sticky="w", pady=2)
        unit_cb.bind("<<ComboboxSelected>>", lambda e: self._on_unit_change())

        self.w_var = tk.StringVar()
        self.h_var = tk.StringVar()
        self.dpi_var = tk.StringVar(value=str(self.tpl.dpi))
        ttk.Label(box, text="宽度").grid(row=2, column=0, sticky="w", pady=2)
        ttk.Spinbox(box, textvariable=self.w_var, from_=16, to=8000, width=8,
                    command=self._apply_card_size).grid(row=2, column=1, sticky="w", pady=2)
        self.w_var.trace_add("write", lambda *a: self._apply_card_size())
        ttk.Label(box, text="高度").grid(row=3, column=0, sticky="w", pady=2)
        ttk.Spinbox(box, textvariable=self.h_var, from_=16, to=8000, width=8,
                    command=self._apply_card_size).grid(row=3, column=1, sticky="w", pady=2)
        self.h_var.trace_add("write", lambda *a: self._apply_card_size())
        ttk.Label(box, text="DPI").grid(row=4, column=0, sticky="w", pady=2)
        ttk.Spinbox(box, textvariable=self.dpi_var, from_=72, to=1200, width=8,
                    command=self._apply_card_size).grid(row=4, column=1, sticky="w", pady=2)
        self.dpi_var.trace_add("write", lambda *a: self._apply_card_size())
        self.size_hint = ttk.Label(box, text="", foreground="#888888")
        self.size_hint.grid(row=5, column=0, columnspan=3, sticky="w", pady=(4, 0))

        bg = ttk.LabelFrame(parent, text="背景与外观", padding=8)
        bg.pack(fill="x", pady=(8, 0))
        ttk.Label(bg, text="背景色").grid(row=0, column=0, sticky="w", pady=2)
        self.bg_color = ColorInput(bg, self.tpl.background_color, allow_empty=False,
                                   on_change=self._set_bg_color, width=10)
        self.bg_color.grid(row=0, column=1, sticky="w", pady=2)
        ttk.Label(bg, text="圆角").grid(row=1, column=0, sticky="w", pady=2)
        self.radius_var = tk.StringVar(value="%g" % self.tpl.corner_radius)
        ttk.Spinbox(bg, textvariable=self.radius_var, from_=0, to=2000, width=8,
                    command=self._apply_bg).grid(row=1, column=1, sticky="w", pady=2)
        self.radius_var.trace_add("write", lambda *a: self._apply_bg())
        ttk.Label(bg, text="描边宽度").grid(row=2, column=0, sticky="w", pady=2)
        self.bw_var = tk.StringVar(value="%g" % self.tpl.border_width)
        ttk.Spinbox(bg, textvariable=self.bw_var, from_=0, to=200, width=8,
                    command=self._apply_bg).grid(row=2, column=1, sticky="w", pady=2)
        self.bw_var.trace_add("write", lambda *a: self._apply_bg())
        ttk.Label(bg, text="描边颜色").grid(row=3, column=0, sticky="w", pady=2)
        self.border_color = ColorInput(bg, self.tpl.border_color,
                                       on_change=self._set_border_color, width=10)
        self.border_color.grid(row=3, column=1, sticky="w", pady=2)
        ttk.Label(bg, text="背景图").grid(row=4, column=0, sticky="w", pady=2)
        row = ttk.Frame(bg)
        row.grid(row=4, column=1, sticky="ew", pady=2)
        self.bgimg_var = tk.StringVar(value=self.tpl.background_image)
        ttk.Entry(row, textvariable=self.bgimg_var, width=18).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="…", width=3, command=self._pick_bg_image).pack(side="left", padx=2)
        ttk.Button(row, text="×", width=3, command=self._clear_bg_image).pack(side="left")
        self.bgimg_var.trace_add("write", lambda *a: self._apply_bg())
        ttk.Label(bg, text="填充方式").grid(row=5, column=0, sticky="w", pady=2)
        self.bgfit_var = tk.StringVar(value=self.tpl.background_fit)
        cb = ttk.Combobox(bg, textvariable=self.bgfit_var, state="readonly", width=10,
                          values=["cover", "contain", "stretch"])
        cb.grid(row=5, column=1, sticky="w", pady=2)
        cb.bind("<<ComboboxSelected>>", lambda e: self._apply_bg())

        tip = ttk.Label(parent, foreground="#888888", justify="left", wraplength=250,
                        text="提示：尺寸支持毫米（mm）单位，配合 DPI 可直接对应印刷尺寸。"
                             "预设里的扑克牌 / 塔罗牌按 300 DPI 换算。")
        tip.pack(fill="x", pady=(10, 0))

    def _build_elements_tab(self, parent):
        bar = ttk.Frame(parent)
        bar.pack(fill="x")
        ttk.Button(bar, text="+ 文本", command=lambda: self.add_element("text")).pack(side="left")
        ttk.Button(bar, text="+ 图片", command=lambda: self.add_element("image")).pack(side="left", padx=4)
        ttk.Button(bar, text="复制", command=self.duplicate_selected).pack(side="left")
        ttk.Button(bar, text="删除", command=self.delete_selected).pack(side="left", padx=4)
        bar2 = ttk.Frame(parent)
        bar2.pack(fill="x", pady=(4, 0))
        ttk.Button(bar2, text="上移", width=6, command=lambda: self.move_selected(-1)).pack(side="left")
        ttk.Button(bar2, text="下移", width=6, command=lambda: self.move_selected(1)).pack(side="left", padx=4)

        ttk.Label(parent, text="元素列表（越靠上越先绘制，会被后画的覆盖）",
                  foreground="#888888").pack(anchor="w", pady=(8, 2))
        self.el_list = tk.Listbox(parent, activestyle="dotbox", exportselection=False)
        self.el_list.pack(fill="both", expand=True)
        self.el_list.bind("<<ListboxSelect>>", self._on_list_select)
        self.el_list.bind("<Double-Button-1>", lambda e: self._rename_selected())

        ttk.Label(parent, text="Excel 自动绑定", foreground="#888888").pack(anchor="w", pady=(10, 2))
        bar3 = ttk.Frame(parent)
        bar3.pack(fill="x")
        ttk.Button(bar3, text="按列名绑定", command=self.auto_bind).pack(side="left")
        ttk.Button(bar3, text="未绑列生成元素", command=self.fill_from_columns).pack(side="left", padx=4)

    def _build_data_tab(self, parent):
        file_row = ttk.Frame(parent)
        file_row.pack(fill="x")
        self.file_var = tk.StringVar()
        ttk.Entry(file_row, textvariable=self.file_var, state="readonly").pack(
            side="left", fill="x", expand=True)
        ttk.Button(file_row, text="选择表格…", command=self.import_excel).pack(side="left", padx=(6, 0))

        opt = ttk.Frame(parent)
        opt.pack(fill="x", pady=6)
        ttk.Label(opt, text="工作表").pack(side="left")
        self.sheet_var = tk.StringVar()
        self.sheet_cb = ttk.Combobox(opt, textvariable=self.sheet_var, state="readonly",
                                     width=14, values=[])
        self.sheet_cb.pack(side="left", padx=(4, 10))
        self.sheet_cb.bind("<<ComboboxSelected>>", lambda e: self.reload_table())
        ttk.Label(opt, text="表头行").pack(side="left")
        self.header_var = tk.StringVar(value="1")
        ttk.Spinbox(opt, from_=1, to=50, textvariable=self.header_var, width=4,
                    command=self.reload_table).pack(side="left", padx=4)
        ttk.Button(opt, text="重新加载", command=self.reload_table).pack(side="left", padx=8)

        ttk.Label(parent, text="数据预览（每行 = 一张卡片）",
                  foreground="#888888").pack(anchor="w", pady=(2, 2))
        tree_frame = ttk.Frame(parent)
        tree_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tree_frame, show="headings", height=12)
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        self.bind_info = ttk.Label(parent, text="", foreground="#555555",
                                   justify="left", wraplength=260)
        self.bind_info.pack(fill="x", pady=(8, 0))

    # ---------------------------------------------------------- 状态
    def _tpl(self) -> CardTemplate:
        return self.tpl

    def current_row_dict(self) -> Dict[str, Any]:
        if 0 <= self.row_index < self.table.row_count:
            return self.table.rows[self.row_index]
        return self._demo_row()

    def _demo_row(self) -> Dict[str, Any]:
        row: Dict[str, Any] = {}
        for el in self.tpl.elements:
            if isinstance(el, TextElement):
                for col in el.content.replace("}}", "{{").split("{{")[1::1]:
                    pass
            if isinstance(el, TextElement) and "{{" in el.content:
                for part in el.content.split("{{")[1:]:
                    col = part.split("}}")[0].strip()
                    if col:
                        row[col] = col
            if isinstance(el, ImageElement) and "{{" in el.source:
                for part in el.source.split("{{")[1:]:
                    col = part.split("}}")[0].strip()
                    if col:
                        row[col] = ""
        return row

    def _preview_row(self) -> Dict[str, Any]:
        return self.current_row_dict()

    # ---------------------------------------------------------- 首次刷新
    def _first_refresh(self):
        self._sync_card_fields()
        self.refresh_elements()
        self.refresh_data_view()
        self.canvas.refresh()
        self.props.set_element(None)
        self._update_status()

    # ---------------------------------------------------------- 模板
    def new_template(self):
        if not messagebox.askokcancel("新建模板", "确定放弃当前模板（未保存的修改将丢失）？"):
            return
        self.tpl = default_template()
        self.current_file = None
        self.selection_reset()
        self.full_refresh()

    def open_template(self):
        path = filedialog.askopenfilename(title="打开卡片模板",
                                          filetypes=[("卡片模板", "*.cardjson *.json"),
                                                     ("所有文件", "*.*")])
        if not path:
            return
        try:
            self.tpl = CardTemplate.load(path)
        except Exception as e:
            messagebox.showerror("打开失败", str(e))
            return
        self.current_file = path
        self.selection_reset()
        self.full_refresh()
        self._set_status(f"已打开模板：{path}")

    def save_template(self):
        if not self.current_file:
            path = filedialog.asksaveasfilename(
                title="保存卡片模板", defaultextension=".cardjson",
                initialfile="我的卡片.cardjson",
                filetypes=[("卡片模板", "*.cardjson"), ("所有文件", "*.*")])
            if not path:
                return
            self.current_file = path
        try:
            self.tpl.save(self.current_file)
        except Exception as e:
            messagebox.showerror("保存失败", str(e))
            return
        self._set_status(f"模板已保存：{self.current_file}")
        self._update_status()

    # ---------------------------------------------------------- Excel
    def import_excel(self):
        path = filedialog.askopenfilename(
            title="选择 Excel / CSV 表格",
            filetypes=[("表格文件", "*.xlsx *.xlsm *.csv *.tsv *.xls"),
                       ("所有文件", "*.*")])
        if not path:
            return
        sheets = list_sheets(path)
        if len(sheets) > 1:
            win = tk.Toplevel(self)
            win.title("选择工作表")
            win.transient(self)
            ttk.Label(win, text="该文件包含多个工作表，请选择：", padding=10).pack()
            var = tk.StringVar(value=sheets[0])
            cb = ttk.Combobox(win, textvariable=var, values=sheets, state="readonly", width=26)
            cb.pack(padx=14, pady=(0, 10))

            def ok():
                win.destroy()
                self._load_table_file(path, var.get())

            ttk.Button(win, text="确定", command=ok).pack(pady=(0, 12))
            win.grab_set()
            self.wait_window(win)
        else:
            self._load_table_file(path, sheets[0] if sheets else None)

    def _load_table_file(self, path: str, sheet: Optional[str] = None):
        try:
            self.table = load_table(path, sheet, int(self.header_var.get() or 1))
        except Exception as e:
            messagebox.showerror("导入失败", f"读取表格失败：\n{e}")
            return
        if not self.table.row_count:
            messagebox.showwarning("导入", "表格里没有数据行")
        self.file_var.set(path)
        self.sheet_var.set(self.table.sheet)
        self.sheet_cb.configure(values=list_sheets(path))
        self.row_index = 0
        self.auto_bind(silent=True)
        self.refresh_data_view()
        self.refresh_elements()
        self._update_row_nav()
        self.canvas.refresh()
        self._set_status(f"已导入 {self.table.row_count} 行数据（每行生成一张卡片）")
        self._update_bind_info()
        self._update_status()

    def reload_table(self):
        path = self.table.path
        if not path or not os.path.isfile(path):
            return
        try:
            self.table = load_table(path, self.sheet_var.get() or None,
                                    int(self.header_var.get() or 1))
        except Exception as e:
            messagebox.showerror("加载失败", str(e))
            return
        self.row_index = min(self.row_index, max(0, self.table.row_count - 1))
        self.refresh_data_view()
        self._update_row_nav()
        self.canvas.refresh()
        self._set_status(f"已重新加载：{self.table.row_count} 行")

    def refresh_data_view(self):
        self.tree.delete(*self.tree.get_children())
        cols = self.table.headers[:14]
        self.tree.configure(columns=cols)
        for c in cols:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=110, anchor="w")
        for i, row in enumerate(self.table.rows[:300]):
            vals = []
            for c in cols:
                v = row.get(c, "")
                if v is None:
                    v = ""
                s = str(v)
                if len(s) > 40:
                    s = s[:40] + "…"
                vals.append(s)
            self.tree.insert("", "end", iid=str(i), text=str(i), values=vals)
        if self.table.row_count:
            self._select_tree_row(self.row_index)

    def _select_tree_row(self, idx: int):
        if 0 <= idx < min(300, self.table.row_count):
            iid = str(idx)
            if self.tree.exists(iid):
                self.tree.selection_set(iid)
                self.tree.see(iid)

    def _on_tree_select(self, _e=None):
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        if idx != self.row_index:
            self.row_index = idx
            self._suspend = True
            try:
                self.row_var.set(str(idx + 1))
            finally:
                self._suspend = False
            self.canvas.refresh()
            self._set_status(f"预览第 {idx + 1} 行")

    def _update_bind_info(self):
        if not self.table.headers:
            self.bind_info.configure(text="尚未导入数据。")
            return
        over = override_columns(self.table.headers)
        parts = []
        for el in self.tpl.elements:
            cov = "、".join(over.get(el.name, []))
            parts.append(f"{el.name} ← {el.name}列" + (f"（+{cov}）" if cov else ""))
        self.bind_info.configure(text="\n".join(parts) if parts else "尚无元素。")

    # ---------------------------------------------------------- 绑定
    def auto_bind(self, silent: bool = False):
        cols = set(base_columns(self.table.headers))
        if not cols:
            if not silent:
                messagebox.showinfo("按列名绑定", "请先导入 Excel")
            return
        n = 0
        for el in self.tpl.elements:
            if el.name in cols:
                if isinstance(el, TextElement):
                    new = "{{%s}}" % el.name
                    if el.content != new and "{{" not in el.content:
                        el.content = new
                        n += 1
                elif isinstance(el, ImageElement):
                    new = "{{%s}}" % el.name
                    if el.source != new and "{{" not in el.source and not el.source:
                        el.source = new
                        n += 1
        self.refresh_elements()
        self.canvas.refresh()
        self._update_bind_info()
        if not silent:
            self._set_status(f"已按列名绑定 {n} 个元素")

    def fill_from_columns(self):
        cols = base_columns(self.table.headers)
        if not cols:
            messagebox.showinfo("生成元素", "请先导入 Excel")
            return
        before = len(self.tpl.elements)
        auto_fill_from_columns(self.tpl, cols, start_y=40)
        self.refresh_elements()
        self.canvas.refresh()
        self._update_bind_info()
        self._set_status(f"新增 {len(self.tpl.elements) - before} 个元素")

    # ---------------------------------------------------------- 元素
    def refresh_elements(self):
        self.el_list.delete(0, "end")
        for el in self.tpl.elements:
            tag = "文" if isinstance(el, TextElement) else "图"
            mark = "" if el.visible else "（隐藏）"
            self.el_list.insert("end", f"[{tag}] {el.name}{mark}")
        if self.canvas.selection:
            idx = next((i for i, e in enumerate(self.tpl.elements) if e.eid == self.canvas.selection), None)
            if idx is not None:
                self.el_list.selection_clear(0, "end")
                self.el_list.selection_set(idx)

    def _on_list_select(self, _e=None):
        sel = self.el_list.curselection()
        if not sel:
            return
        el = self.tpl.elements[sel[0]]
        self.canvas.selection = el.eid
        self.props.set_element(el)
        self.canvas.refresh()

    def _on_canvas_select(self, eid: Optional[str]):
        el = self.tpl.element_by_id(eid) if eid else None
        self.props.set_element(el)
        self.refresh_elements()

    def _on_canvas_changed(self):
        el = self.tpl.element_by_id(self.canvas.selection) if self.canvas.selection else None
        if el:
            self.props.sync_from_element(el)
        self._update_bind_info()

    def add_element(self, kind: str):
        if kind == "text":
            el: Element = TextElement(
                name="新文本", content="双击左侧列表可重命名",
                x=60, y=60 + 26 * (len(self.tpl.elements) % 10),
                width=max(120.0, self.tpl.width - 120), font_size=32)
        else:
            el = ImageElement(name="新图片", source="", x=80, y=80,
                              width=max(120.0, self.tpl.width / 4),
                              height=max(120.0, self.tpl.width / 4), fit="cover")
        self.tpl.add_element(el)
        self.canvas.selection = el.eid
        self.refresh_elements()
        self.props.set_element(el)
        self.canvas.refresh()
        self._set_status(f"已添加{('文本' if kind == 'text' else '图片')}元素，双击元素列表可重命名")

    def delete_selected(self):
        eid = self.canvas.selection
        if not eid:
            return
        el = self.tpl.element_by_id(eid)
        if el and messagebox.askokcancel("删除元素", f"确定删除元素「{el.name}」？"):
            self.tpl.remove_element(eid)
            self.selection_reset()
            self.full_refresh()

    def duplicate_selected(self):
        eid = self.canvas.selection
        if not eid:
            return
        el = self.tpl.element_by_id(eid)
        if not el:
            return
        new = copy.deepcopy(el)
        new.eid = ""
        new.x += 16
        new.y += 16
        self.tpl.add_element(new)
        self.canvas.selection = new.eid
        self.refresh_elements()
        self.props.set_element(new)
        self.canvas.refresh()

    def move_selected(self, delta: int):
        eid = self.canvas.selection
        if not eid:
            return
        self.tpl.move_element(eid, delta)
        self.refresh_elements()
        self.canvas.refresh()

    def _rename_selected(self):
        eid = self.canvas.selection
        if not eid:
            return
        el = self.tpl.element_by_id(eid)
        if not el:
            return
        win = tk.Toplevel(self)
        win.title("重命名元素")
        win.transient(self)
        ttk.Label(win, text="元素名（同时是绑定的 Excel 列名）", padding=(10, 8)).pack()
        var = tk.StringVar(value=el.name)
        e = ttk.Entry(win, textvariable=var, width=28)
        e.pack(padx=12, pady=(0, 10))
        e.focus_set()
        e.select_range(0, "end")

        def ok():
            name = var.get().strip()
            if name:
                el.name = name
                self.refresh_elements()
                self.props.set_element(el)
                self._update_bind_info()
            win.destroy()

        ttk.Button(win, text="确定", command=ok).pack(pady=(0, 12))
        win.bind("<Return>", lambda ev: ok())
        win.grab_set()

    def selection_reset(self):
        self.canvas.selection = None
        self.props.set_element(None)

    def full_refresh(self):
        self._sync_card_fields()
        self.refresh_elements()
        self.refresh_data_view()
        self._update_row_nav()
        self._update_bind_info()
        self.canvas.refresh()
        self._update_status()

    # ---------------------------------------------------------- 卡片设置
    def _sync_card_fields(self):
        self._suspend = True
        try:
            dpi = self.tpl.dpi
            self.w_var.set("%g" % round(from_px(self.tpl.width, self.unit, dpi), 2))
            self.h_var.set("%g" % round(from_px(self.tpl.height, self.unit, dpi), 2))
            self.dpi_var.set(str(dpi))
            self.radius_var.set("%g" % self.tpl.corner_radius)
            self.bw_var.set("%g" % self.tpl.border_width)
            self.bg_color.set(self.tpl.background_color)
            self.border_color.set(self.tpl.border_color)
            self.bgimg_var.set(self.tpl.background_image)
            self.bgfit_var.set(self.tpl.background_fit)
            wmm = self.tpl.width / dpi * 25.4
            hmm = self.tpl.height / dpi * 25.4
            self.size_hint.configure(
                text=f"实际输出：{int(self.tpl.width)}×{int(self.tpl.height)} px ≈ {wmm:.1f}×{hmm:.1f} mm")
        finally:
            self._suspend = False

    def _on_unit_change(self):
        old = self.unit
        new = self.unit_var.get()
        if new == old:
            return
        dpi = self.tpl.dpi
        w = to_px(float(self.w_var.get() or 0), old, dpi)
        h = to_px(float(self.h_var.get() or 0), old, dpi)
        self.unit = new
        self._suspend = True
        self.w_var.set("%g" % round(from_px(w, new, dpi), 2))
        self.h_var.set("%g" % round(from_px(h, new, dpi), 2))
        self._suspend = False
        self.size_hint.configure(
            text=f"实际输出：{int(self.tpl.width)}×{int(self.tpl.height)} px")

    def _on_preset(self, _e=None):
        name = self.preset_var.get()
        for p in CARD_PRESETS:
            if p["name"] == name:
                dpi = int(self.dpi_var.get() or 300)
                self.unit_var.set(p["unit"])
                self.unit = p["unit"]
                self.tpl.dpi = dpi
                self.tpl.width = round(to_px(p["w"], p["unit"], dpi))
                self.tpl.height = round(to_px(p["h"], p["unit"], dpi))
                self._sync_card_fields()
                self.canvas.set_zoom_mode("fit")
                break

    def _apply_card_size(self, *_a):
        if self._suspend:
            return
        try:
            dpi = max(36, min(2400, int(float(self.dpi_var.get() or 300))))
        except ValueError:
            dpi = self.tpl.dpi
        try:
            w = to_px(float(self.w_var.get() or self.tpl.width), self.unit, dpi)
            h = to_px(float(self.h_var.get() or self.tpl.height), self.unit, dpi)
        except ValueError:
            return
        self.tpl.dpi = dpi
        self.tpl.width = max(16.0, min(12000.0, w))
        self.tpl.height = max(16.0, min(12000.0, h))
        self.canvas.schedule_refresh(120)
        self._update_status()

    def _apply_bg(self, *_a):
        if self._suspend:
            return
        try:
            self.tpl.corner_radius = max(0.0, float(self.radius_var.get() or 0))
        except ValueError:
            pass
        try:
            self.tpl.border_width = max(0.0, float(self.bw_var.get() or 0))
        except ValueError:
            pass
        self.tpl.background_image = self.bgimg_var.get().strip()
        self.tpl.background_fit = self.bgfit_var.get()
        self.canvas.schedule_refresh(120)

    def _set_bg_color(self, value: str):
        if self._suspend:
            return
        self.tpl.background_color = value or "#FFFFFF"
        self.canvas.schedule_refresh(120)

    def _set_border_color(self, value: str):
        if self._suspend:
            return
        self.tpl.border_color = value or "#000000"
        self.canvas.schedule_refresh(120)

    def _pick_bg_image(self):
        path = filedialog.askopenfilename(title="选择背景图",
                                          filetypes=[("图片", "*.png *.jpg *.jpeg *.webp *.bmp"),
                                                     ("所有文件", "*.*")])
        if path:
            self.bgimg_var.set(path)

    def _clear_bg_image(self):
        self.bgimg_var.set("")

    # ---------------------------------------------------------- 属性联动
    def _on_prop_changed(self):
        self.canvas.schedule_refresh(60)
        self._update_bind_info()

    def _align_element(self, action: str):
        el = self.tpl.element_by_id(self.canvas.selection) if self.canvas.selection else None
        if not el:
            return
        ink = self.canvas.renderer.last_bounds.get(el.eid)
        w = ink[2] if ink else (el.width or 100)
        h = ink[3] if ink else (el.height or 40)
        cw, ch = self.tpl.width, self.tpl.height
        if action == "hcenter":
            el.x = (cw - w) / 2
        elif action == "vcenter":
            el.y = (ch - h) / 2
        elif action == "left":
            el.x = 0.0
        elif action == "right":
            el.x = cw - w
        elif action == "top":
            el.y = 0
        elif action == "bottom":
            el.y = ch - h
        self.canvas.refresh()
        self.props.sync_from_element(el)

    # ---------------------------------------------------------- 行导航
    def go_row(self, delta: int):
        if not self.table.row_count:
            return
        self.row_index = max(0, min(self.table.row_count - 1, self.row_index + delta))
        self._suspend = True
        self.row_var.set(str(self.row_index + 1))
        self._suspend = False
        self._select_tree_row(self.row_index)
        self.canvas.refresh()
        self._set_status(f"预览第 {self.row_index + 1} / {self.table.row_count} 行")

    def _on_row_spin(self):
        if self._suspend:
            return
        try:
            idx = int(self.row_var.get()) - 1
        except ValueError:
            return
        if 0 <= idx < max(1, self.table.row_count):
            self.row_index = idx
            self._select_tree_row(idx)
            self.canvas.refresh()

    def _update_row_nav(self):
        n = self.table.row_count
        self.row_total.configure(text=f"/ {n} 行")
        self.row_spin.configure(to=max(1, n))
        self._suspend = True
        self.row_var.set(str(min(n, self.row_index + 1) if n else 0))
        self._suspend = False

    # ---------------------------------------------------------- 导出
    def export_current(self):
        row = self.current_row_dict()
        path = filedialog.asksaveasfilename(
            title="导出当前卡片", defaultextension=".png",
            initialfile=build_name("{index:03d}_{标题}", self.row_index + 1, row) + ".png",
            filetypes=[("PNG 图片", "*.png"), ("JPG 图片", "*.jpg"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            item = apply_row(self.tpl, row)
            img = self.canvas.renderer.render(item, row)
            ext = os.path.splitext(path)[1].lower()
            if ext in (".jpg", ".jpeg"):
                bg = Image.new("RGB", img.size, (255, 255, 255))
                bg.paste(img, mask=img.getchannel("A"))
                bg.save(path, "JPEG", quality=95)
            else:
                img.save(path, "PNG", dpi=(self.tpl.dpi, self.tpl.dpi))
        except Exception as e:
            messagebox.showerror("导出失败", str(e))
            return
        self._set_status(f"已导出：{path}")

    def batch_export(self):
        if not self.table.row_count:
            messagebox.showinfo("批量导出", "请先在左侧「数据」页签导入 Excel 表格。")
            return
        ExportDialog(self)

    def run_with_progress(self, title: str, job: Callable[[Callable], Any],
                          on_done: Callable[[Any], None]):
        q: "queue.Queue[Any]" = queue.Queue()
        cancel = threading.Event()

        def progress(done, total, msg):
            q.put((done, total, msg))
            return not cancel.is_set()

        dlg = ProgressDialog(self, title)

        def worker():
            try:
                q.put(("done", job(progress)))
            except Exception as e:  # noqa: BLE001
                q.put(("error", e))

        threading.Thread(target=worker, daemon=True).start()

        def poll():
            try:
                while True:
                    item = q.get_nowait()
                    if item[0] == "done":
                        dlg.close()
                        on_done(item[1])
                        return
                    if item[0] == "error":
                        dlg.close()
                        messagebox.showerror(title, f"处理失败：\n{item[1]}")
                        return
                    done, total, msg = item
                    if not dlg.step(done, total, msg):
                        cancel.set()
            except queue.Empty:
                pass
            self.after(80, poll)

        poll()

    @staticmethod
    def _noop(_res):
        pass

    # ---------------------------------------------------------- 其他
    def _toggle_boxes(self):
        val = self._boxes_var.get()
        self.canvas.show_boxes = val
        self.canvas.refresh()

    def _bind_keys(self):
        self.bind("<Control-n>", lambda e: self.new_template())
        self.bind("<Control-o>", lambda e: self.open_template())
        self.bind("<Control-s>", lambda e: self.save_template())
        self.bind("<Control-i>", lambda e: self.import_excel())
        self.bind("<Control-d>", lambda e: self.duplicate_selected())
        self.bind("<Delete>", lambda e: self.delete_selected())
        self.bind("<Left>", lambda e: (self.canvas.nudge(-1, 0), "break"))
        self.bind("<Right>", lambda e: (self.canvas.nudge(1, 0), "break"))
        self.bind("<Up>", lambda e: (self.canvas.nudge(0, -1), "break"))
        self.bind("<Down>", lambda e: (self.canvas.nudge(0, 1), "break"))
        self.bind("<Shift-Left>", lambda e: (self.canvas.nudge(-10, 0), "break"))
        self.bind("<Shift-Right>", lambda e: (self.canvas.nudge(10, 0), "break"))
        self.bind("<Shift-Up>", lambda e: (self.canvas.nudge(0, -10), "break"))
        self.bind("<Shift-Down>", lambda e: (self.canvas.nudge(0, 10), "break"))
        self.bind("<Prior>", lambda e: self.go_row(-1))
        self.bind("<Next>", lambda e: self.go_row(1))

    def _set_status(self, text: str):
        self.status.configure(text=text)

    def _update_status(self):
        self.status_right.configure(
            text=f"{self.tpl.width:.0f}×{self.tpl.height:.0f}px @ {self.tpl.dpi}dpi　"
                 f"{len(self.tpl.elements)} 个元素　"
                 f"数据：{os.path.basename(self.table.path) if self.table.path else '未导入'}")
        self.title(f"{APP_TITLE} - {self.tpl.name}"
                   + (f"（{os.path.basename(self.current_file)}）" if self.current_file else ""))

    # ---------------------------------------------------------- 示例 / 帮助
    def make_samples(self):
        base = os.path.dirname(os.path.abspath(self.current_file or "samples")) \
            if self.current_file else os.getcwd()
        try:
            xlsx, img_dir = samples.generate(base)
        except Exception as e:
            messagebox.showerror("生成示例失败", str(e))
            return
        self._load_table_file(xlsx)
        messagebox.showinfo("生成示例数据",
                            f"已生成示例表格与配图：\n{xlsx}\n{img_dir}\n\n已自动导入，可直接批量导出体验。")

    def show_help(self):
        HelpWindow(self)


HELP_TEXT = """卡片制作器使用说明

一、基本流程
1. 「卡片」页签：设定卡片尺寸（可用 mm 毫米单位）、背景、圆角、描边。
2. 「元素」页签：添加文本 / 图片元素，点选后在右侧属性面板调整
   坐标、字号、加粗、斜体、颜色等；也可以直接在画布上拖拽。
3. 「数据」页签：导入 Excel（.xlsx / .csv），每一行就是一张卡片。
4. 用画布下方的 ◀ ▶ 按钮逐行预览，确认效果。
5. 「批量导出」把每一行渲染成 PNG / JPG / PDF。

二、Excel 列名约定（重点）
设某个元素叫「标题」：
  标题          → 该元素的内容（图片元素则是本地文件路径）
  标题.x / 标题.y     → 逐行覆盖坐标
  标题.size     → 覆盖字号          标题.bold / 标题.italic → 覆盖粗体 / 斜体
  标题.color    → 覆盖颜色          标题.font   → 覆盖字体（字体名或字体文件路径）
  标题.w / 标题.h     → 覆盖文本框宽高    标题.align / 标题.valign → 对齐
  标题.spacing  → 行距              标题.rotate → 旋转角度
  标题.opacity  → 不透明度          标题.fit / 标题.radius → 图片填充 / 圆角
下划线写法同样有效：标题_x、标题_size。
没有任何后缀的列会作为对应元素的内容列。

三、{{占位符}}
文本内容支持 {{列名}} 占位符，例如：
  「{{姓名}} 的攻击力 {{攻击力}}」
渲染时会替换成每一行的对应值。

四、图片元素
「图片」列填本地文件路径（绝对路径，或相对 Excel 所在目录的路径），
每行可以指向不同的图片文件。

五、打包成 exe
在项目目录运行：python build.py
生成 dist/卡片制作器/卡片制作器.exe（免安装，可拷贝到其他电脑）。

六、快捷键
Ctrl+N/O/S 新建 / 打开 / 保存模板；Ctrl+I 导入 Excel；
方向键微调选中元素（Shift+方向键移动 10px）；
Delete 删除元素；PageUp / PageDown 切换行。
"""


class HelpWindow(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title("使用说明")
        self.geometry("660x620")
        txt = tk.Text(self, wrap="word", padx=14, pady=12, relief="flat")
        sb = ttk.Scrollbar(self, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        txt.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        txt.insert("1.0", HELP_TEXT)
        txt.configure(state="disabled")


def main():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    app = CardMakerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
