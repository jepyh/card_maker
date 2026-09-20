"""卡片画布：实时预览 + 拖拽编辑。"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, Optional, Tuple

from PIL import Image, ImageTk

from ..model import Element, TextElement
from ..renderer import CardRenderer

CANVAS_BG = "#E9E9EE"
GRID_COLOR = "#C9C9D2"
SEL_COLOR = "#E8442C"
BOX_COLOR = "#2F7FD6"
MARGIN = 28


class CardCanvas(ttk.Frame):
    def __init__(self, master, get_template: Callable[[], object],
                 get_row: Callable[[], Dict[str, object]],
                 on_select: Callable[[Optional[str]], None],
                 on_changed: Callable[[], None]):
        super().__init__(master)
        self.get_template = get_template
        self.get_row = get_row
        self.on_select = on_select
        self.on_changed = on_changed

        self.renderer = CardRenderer()
        self.zoom_mode = "fit"
        self.zoom = 1.0
        self.selection: Optional[str] = None
        self.show_boxes = True
        self._photo: Optional[ImageTk.PhotoImage] = None
        self._img_id: Optional[int] = None
        self._outline_ids: list = []
        self._drag = None
        self._render_job: Optional[str] = None
        self._suspend = False

        self.canvas = tk.Canvas(self, bg=CANVAS_BG, highlightthickness=0, bd=0)
        self.hbar = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=self.hbar.set, yscrollcommand=self.vbar.set)

        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vbar.grid(row=0, column=1, sticky="ns")
        self.hbar.grid(row=1, column=0, sticky="ew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Configure>", lambda e: self._on_resize())
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Control-MouseWheel>", self._on_ctrl_wheel)
        self.canvas.bind("<Shift-MouseWheel>", lambda e: self.canvas.xview_scroll(int(-e.delta / 120), "units"))
        self.canvas.bind("<Motion>", self._on_hover)
        self.canvas.focus_set()

    # ------------------------------------------------------------ 渲染
    def schedule_refresh(self, delay: int = 30) -> None:
        if self._render_job:
            try:
                self.after_cancel(self._render_job)
            except Exception:
                pass
        self._render_job = self.after(delay, self.refresh)

    def refresh(self) -> None:
        self._render_job = None
        tpl = self.get_template()
        row = self.get_row()
        try:
            img = self.renderer.render(tpl, row)
        except Exception:
            return
        card_w, card_h = img.size

        self.zoom = self._compute_zoom(card_w, card_h)
        disp_w = max(1, int(round(card_w * self.zoom)))
        disp_h = max(1, int(round(card_h * self.zoom)))
        if (disp_w, disp_h) != (card_w, card_h):
            disp = img.resize((disp_w, disp_h), Image.LANCZOS)
        else:
            disp = img
        # 白底压一层，避免透明卡角在浅色画布上看不清
        flat = Image.new("RGB", disp.size, (255, 255, 255))
        flat.paste(disp, mask=disp.convert("RGBA").getchannel("A"))
        self._photo = ImageTk.PhotoImage(flat)

        self.canvas.delete("all")
        self._outline_ids = []
        total_w = disp_w + MARGIN * 2
        total_h = disp_h + MARGIN * 2
        self.canvas.configure(scrollregion=(0, 0, total_w, total_h))

        # 阴影 + 卡面
        self.canvas.create_rectangle(MARGIN + 4, MARGIN + 4, MARGIN + disp_w + 4, MARGIN + disp_h + 4,
                                     fill="#C8C8D0", outline="")
        self._img_id = self.canvas.create_image(MARGIN, MARGIN, image=self._photo, anchor="nw")
        self.canvas.create_rectangle(MARGIN, MARGIN, MARGIN + disp_w, MARGIN + disp_h,
                                     outline="#9A9AA6", width=1)
        self._draw_outlines(tpl)

    def _compute_zoom(self, card_w: int, card_h: int) -> float:
        if self.zoom_mode == "fit":
            avail_w = max(80, self.canvas.winfo_width() - MARGIN * 2 - 8)
            avail_h = max(80, self.canvas.winfo_height() - MARGIN * 2 - 8)
            return max(0.05, min(avail_w / card_w, avail_h / card_h, 1.6))
        return float(self.zoom)

    def set_zoom_mode(self, mode) -> None:
        if mode == "fit":
            self.zoom_mode = "fit"
        else:
            self.zoom_mode = "manual"
            self.zoom = float(mode)
        self.refresh()

    def zoom_by(self, factor: float) -> None:
        base = self.zoom if self.zoom_mode == "manual" else self._compute_zoom(
            int(self.get_template().width), int(self.get_template().height))
        self.zoom_mode = "manual"
        self.zoom = max(0.05, min(6.0, base * factor))
        self.refresh()

    def _on_resize(self) -> None:
        if self.zoom_mode == "fit":
            self.schedule_refresh(10)

    # ------------------------------------------------------------ 元素框
    def _box_of(self, el: Element, ink: Optional[Tuple[float, float, float, float]]) -> Tuple[float, float, float, float]:
        w = float(el.width) if el.width and el.width > 0 else (ink[2] if ink else 80.0)
        h = float(el.height) if el.height and el.height > 0 else (ink[3] if ink else 30.0)
        return (float(el.x), float(el.y), max(1.0, w), max(1.0, h))

    def _draw_outlines(self, tpl) -> None:
        if not self.show_boxes:
            return
        z = self.zoom
        for el in tpl.elements:
            ink = self.renderer.last_bounds.get(el.eid)
            bx, by, bw, bh = self._box_of(el, ink)
            sel = (el.eid == self.selection)
            color = SEL_COLOR if sel else BOX_COLOR
            dash = () if sel else (4, 3)
            self._outline_ids.append(self.canvas.create_rectangle(
                MARGIN + bx * z, MARGIN + by * z,
                MARGIN + (bx + bw) * z, MARGIN + (by + bh) * z,
                outline=color, width=2 if sel else 1, dash=dash,
                tags=(f"el:{el.eid}", "outline")))
            if not el.visible:
                self._outline_ids.append(self.canvas.create_text(
                    MARGIN + bx * z + 6, MARGIN + by * z + 12, anchor="w",
                    text="已隐藏", fill="#999999"))
            if sel:
                # 右下角缩放柄
                hx = MARGIN + (bx + bw) * z
                hy = MARGIN + (by + bh) * z
                self._outline_ids.append(self.canvas.create_rectangle(
                    hx - 5, hy - 5, hx + 5, hy + 5, fill=SEL_COLOR, outline="#FFFFFF",
                    tags=("handle",)))
                if ink:
                    self._outline_ids.append(self.canvas.create_rectangle(
                        MARGIN + ink[0] * z, MARGIN + ink[1] * z,
                        MARGIN + (ink[0] + ink[2]) * z, MARGIN + (ink[1] + ink[3]) * z,
                        outline="#8E8E99", dash=(2, 2)))

    # ------------------------------------------------------------ 坐标换算
    def _to_card(self, sx: float, sy: float) -> Tuple[float, float]:
        z = self.zoom or 1.0
        return ((sx - MARGIN) / z, (sy - MARGIN) / z)

    def _pick(self, cx: float, cy: float) -> Optional[Element]:
        tpl = self.get_template()
        for el in reversed(tpl.elements):
            ink = self.renderer.last_bounds.get(el.eid)
            bx, by, bw, bh = self._box_of(el, ink)
            if bx <= cx <= bx + bw and by <= cy <= by + bh:
                return el
        return None

    def _handle_hit(self, cx: float, cy: float) -> bool:
        if not self.selection:
            return False
        tpl = self.get_template()
        el = tpl.element_by_id(self.selection)
        if not el:
            return False
        ink = self.renderer.last_bounds.get(el.eid)
        bx, by, bw, bh = self._box_of(el, ink)
        tol = 8.0 / max(0.05, self.zoom)
        return abs(cx - (bx + bw)) <= tol and abs(cy - (by + bh)) <= tol

    # ------------------------------------------------------------ 鼠标
    def _on_press(self, event) -> None:
        self.canvas.focus_set()
        cx, cy = self._to_card(event.x, event.y)
        tpl = self.get_template()
        if self._handle_hit(cx, cy):
            el = tpl.element_by_id(self.selection)
            ink = self.renderer.last_bounds.get(el.eid) if el else None
            bx, by, bw, bh = self._box_of(el, ink) if el else (0, 0, 0, 0)
            self._drag = {"mode": "resize", "el": el, "x0": cx, "y0": cy,
                          "w0": bw, "h0": bh,
                          "has_w": bool(el.width and el.width > 0),
                          "has_h": bool(el.height and el.height > 0)}
            return
        el = self._pick(cx, cy)
        if el is None:
            self.selection = None
            self.on_select(None)
            self._draw_outlines(tpl)
            return
        if el.eid != self.selection:
            self.selection = el.eid
            self.on_select(el.eid)
        self._drag = {"mode": "move", "el": el, "x0": cx, "y0": cy,
                      "ex0": float(el.x), "ey0": float(el.y), "moved": False}
        self._draw_outlines(tpl)

    def _on_motion(self, event) -> None:
        if not self._drag:
            return
        cx, cy = self._to_card(event.x, event.y)
        el = self._drag["el"]
        if el is None:
            return
        step = 1.0
        if self._drag["mode"] == "move":
            dx = round((cx - self._drag["x0"]) / step) * step
            dy = round((cy - self._drag["y0"]) / step) * step
            el.x = self._drag["ex0"] + dx
            el.y = self._drag["ey0"] + dy
            self._drag["moved"] = True
        else:
            nw = max(1.0, self._drag["w0"] + (cx - self._drag["x0"]))
            nh = max(1.0, self._drag["h0"] + (cy - self._drag["y0"]))
            if self._drag["has_w"] or isinstance(el, TextElement):
                el.width = round(nw)
            if self._drag["has_h"] or isinstance(el, TextElement):
                el.height = round(nh)
            self._drag["moved"] = True
        self.schedule_refresh(40)

    def _on_release(self, _event) -> None:
        if self._drag and self._drag.get("moved"):
            self.refresh()
            self.on_changed()
        self._drag = None

    def _on_hover(self, event) -> None:
        cx, cy = self._to_card(event.x, event.y)
        if self._handle_hit(cx, cy):
            self.canvas.configure(cursor="size_nw_se")
        elif self._pick(cx, cy) is not None:
            self.canvas.configure(cursor="fleur")
        else:
            self.canvas.configure(cursor="")

    def _on_wheel(self, event) -> None:
        self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def _on_ctrl_wheel(self, event) -> None:
        self.zoom_by(1.1 if event.delta > 0 else 1 / 1.1)

    # ------------------------------------------------------------ 键盘微调
    def nudge(self, dx: float, dy: float) -> None:
        tpl = self.get_template()
        el = tpl.element_by_id(self.selection) if self.selection else None
        if not el:
            return
        el.x = float(el.x) + dx
        el.y = float(el.y) + dy
        self.refresh()
        self.on_changed()
