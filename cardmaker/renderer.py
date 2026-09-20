"""卡片渲染引擎：把 CardTemplate + 一行数据 渲染成一张 Pillow 图像。"""
from __future__ import annotations

import os
import re
import threading
from functools import lru_cache
from typing import Any, Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont, ImageOps

from . import fonts as fontlib
from .model import (
    CardTemplate,
    Element,
    ImageElement,
    TextElement,
    color_to_rgba,
    normalize_color,
    num,
)

PLACEHOLDER_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff")

_CJK_RANGES = (
    (0x1100, 0x11FF), (0x2E80, 0x2EFF), (0x3000, 0x303F), (0x3040, 0x30FF),
    (0x3130, 0x318F), (0x31C0, 0x31EF), (0x3400, 0x4DBF), (0x4E00, 0x9FFF),
    (0xA960, 0xA97F), (0xAC00, 0xD7AF), (0xF900, 0xFAFF), (0xFE30, 0xFE4F),
    (0xFF00, 0xFFEF),
)
_NO_LINE_START = "，。、；：？！）】》」』”’%,.;:?!)]}>"
_NO_LINE_END = "（【《「『“‘([{<"


def is_cjk(ch: str) -> bool:
    o = ord(ch)
    return any(a <= o <= b for a, b in _CJK_RANGES)


# ----------------------------------------------------------------- 文本取值
def render_string(text: str, row: Dict[str, Any], strict: bool = False) -> str:
    """把 {{列名}} 替换成行数据。strict=False 时缺失的列原样保留。"""
    if not text:
        return ""
    if not PLACEHOLDER_RE.search(text):
        return text

    def sub(m: "re.Match[str]") -> str:
        key = m.group(1)
        if key in row:
            v = row[key]
            return "" if v is None else str(v)
        if strict:
            return ""
        return m.group(0)

    return PLACEHOLDER_RE.sub(sub, text)


def used_columns(text: str) -> List[str]:
    return PLACEHOLDER_RE.findall(text or "")


# ----------------------------------------------------------------- 字体缓存
@lru_cache(maxsize=256)
def _load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def get_font(path: Optional[str], size: int) -> ImageFont.FreeTypeFont:
    size = max(1, int(round(size)))
    candidates = [path] if path else []
    candidates += [fontlib.default_font_path(), "arial.ttf", "DejaVuSans.ttf"]
    for c in candidates:
        if not c:
            continue
        try:
            return _load_font(c, size)
        except Exception:
            continue
    # 最后的兜底：PIL 内置位图字体
    return ImageFont.load_default()


# ----------------------------------------------------------------- 度量工具
@lru_cache(maxsize=4096)
def _char_width(font_path: Optional[str], size: int, ch: str) -> float:
    f = get_font(font_path, size)
    try:
        return f.getlength(ch)
    except Exception:
        return float(size)


def measure_text(font: ImageFont.FreeTypeFont, text: str, letter_spacing: float = 0.0,
                 font_path: Optional[str] = None, size: int = 0) -> float:
    if not text:
        return 0.0
    if letter_spacing == 0:
        try:
            return float(font.getlength(text))
        except Exception:
            pass
    total = 0.0
    for i, ch in enumerate(text):
        try:
            total += float(font.getlength(ch))
        except Exception:
            total += size or 10
        if i:
            total += letter_spacing
    return total


def _tokenize(text: str) -> List[str]:
    """把字符串切成可换行单元：CJK 逐字，西文按词。"""
    tokens: List[str] = []
    buf = ""
    for ch in text:
        if is_cjk(ch):
            if buf:
                tokens.append(buf)
                buf = ""
            tokens.append(ch)
        elif ch == " ":
            if buf:
                tokens.append(buf)
                buf = ""
            tokens.append(" ")
        else:
            buf += ch
    if buf:
        tokens.append(buf)
    return tokens


def wrap_text(text: str, max_w: float, font: ImageFont.FreeTypeFont,
              letter_spacing: float = 0.0, font_path: Optional[str] = None,
              size: int = 0) -> List[str]:
    """按像素宽度贪心折行，尽量不在行首/行尾留下标点。"""
    if max_w <= 0:
        return [text]
    out: List[str] = []
    for para in text.split("\n"):
        if para == "":
            out.append("")
            continue
        line = ""
        for tok in _tokenize(para):
            trial = line + tok
            w = measure_text(font, trial, letter_spacing, font_path, size)
            if w <= max_w or line == "":
                # 行首避让：若换行后首字符是禁则标点，则把它留在上一行
                if line and tok and tok[0] in _NO_LINE_START:
                    line += tok
                    continue
                if line == "" and tok.strip() == "":
                    continue
                line = trial
                if w > max_w and len(tok) > 1:
                    # 单个词就超宽 -> 硬拆
                    line = ""
                    for ch in tok:
                        if measure_text(font, line + ch, letter_spacing, font_path, size) > max_w and line:
                            out.append(line)
                            line = ch
                        else:
                            line += ch
            else:
                # 标点悬挂：行首禁则标点直接挂到上一行末尾（允许轻微超宽）
                if line and tok and tok[0] in _NO_LINE_START:
                    line += tok
                    continue
                if line and tok != " " and line[-1] in _NO_LINE_END:
                    out.append(line + tok)
                    line = ""
                    continue
                out.append(line.rstrip())
                line = tok if tok != " " else ""
        out.append(line.rstrip())
    return out


def _ellipsize(line: str, max_w: float, font: ImageFont.FreeTypeFont, letter_spacing: float,
               font_path: Optional[str], size: int) -> str:
    ell = "…"
    if measure_text(font, line + ell, letter_spacing, font_path, size) <= max_w:
        return line + ell
    s = line
    while s and measure_text(font, s + ell, letter_spacing, font_path, size) > max_w:
        s = s[:-1]
    return s + ell


# ----------------------------------------------------------------- 图像工具
def apply_opacity(img: Image.Image, opacity: float) -> Image.Image:
    if opacity >= 0.999:
        return img
    img = img.convert("RGBA")
    a = img.getchannel("A").point(lambda v: int(v * max(0.0, min(1.0, opacity))))
    img.putalpha(a)
    return img


def rounded_mask(size: Tuple[int, int], radius: float) -> Image.Image:
    w, h = max(1, int(size[0])), max(1, int(size[1]))
    mask = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(mask)
    r = max(0, min(float(radius), min(w, h) / 2))
    if r <= 0.5:
        d.rectangle([0, 0, w - 1, h - 1], fill=255)
    else:
        d.rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=255)
    return mask


def _shear_italic(img: Image.Image, factor: float = 0.21) -> Image.Image:
    """合成斜体：字形顶部相对底部向右倾斜。

    反向映射 in_x = out_x + factor*out_y - factor*h，
    即 out_x = in_x + factor*(h - sy)，顶部右移、底部不动。
    """
    w, h = img.size
    if w < 1 or h < 1:
        return img
    extra = int(abs(factor) * h) + 2
    new_w = w + extra
    data = (1, factor, -factor * h, 0, 1, 0)
    try:
        out = img.transform((new_w, h), Image.Transform.AFFINE, data,
                            resample=Image.BICUBIC)
    except Exception:
        return img
    return out


def _trim_alpha(img: Image.Image) -> Tuple[Image.Image, int, int]:
    bbox = img.getbbox()
    if not bbox:
        return img, 0, 0
    return img.crop(bbox), bbox[0], bbox[1]


# ----------------------------------------------------------------- 渲染器
class CardRenderer:
    def __init__(self, image_cache_size: int = 64):
        self._img_cache: Dict[str, Image.Image] = {}
        self._cache_order: List[str] = []
        self._cache_size = image_cache_size
        self._lock = threading.Lock()
        # 最近一次 render 中每个元素的墨迹包围盒 {eid: (x, y, w, h)}，供界面框选使用
        self.last_bounds: Dict[str, Tuple[float, float, float, float]] = {}

    # ---- 图片缓存 ----
    def _load_image(self, path: str) -> Optional[Image.Image]:
        if not path or not os.path.isfile(path):
            return None
        try:
            key = f"{path}|{os.path.getmtime(path)}"
        except OSError:
            return None
        with self._lock:
            if key in self._img_cache:
                return self._img_cache[key]
        try:
            img = Image.open(path)
            img.load()
            img = img.convert("RGBA")
        except Exception:
            return None
        with self._lock:
            self._img_cache[key] = img
            self._cache_order.append(key)
            while len(self._cache_order) > self._cache_size:
                old = self._cache_order.pop(0)
                self._img_cache.pop(old, None)
        return img

    def clear_cache(self) -> None:
        with self._lock:
            self._img_cache.clear()
            self._cache_order.clear()

    # ---- 主入口 ----
    def render(self, tpl: CardTemplate, row: Optional[Dict[str, Any]] = None) -> Image.Image:
        row = row or {}
        w = max(1, int(round(tpl.width)))
        h = max(1, int(round(tpl.height)))
        canvas = Image.new("RGBA", (w, h), color_to_rgba(tpl.background_color or "#FFFFFF"))

        # 背景图
        if tpl.background_image:
            bg_path = render_string(tpl.background_image, row)
            if not os.path.isabs(bg_path):
                bg_path = os.path.abspath(bg_path)
            bg = self._load_image(bg_path)
            if bg is not None:
                canvas = self._place_image(
                    canvas, bg, (0, 0, w, h), tpl.background_fit, 0.0, 1.0, 0.0, 0.0
                )

        # 元素
        self.last_bounds = {}
        for el in tpl.elements:
            if not el.visible:
                continue
            try:
                if isinstance(el, TextElement):
                    layer, pos = self._render_text(el, row)
                elif isinstance(el, ImageElement):
                    layer, pos = self._render_image(el, row)
                else:
                    continue
            except Exception:
                continue
            if layer is None:
                continue
            layer = apply_opacity(layer, el.opacity)
            self.last_bounds[el.eid] = (float(pos[0]), float(pos[1]),
                                        float(layer.width), float(layer.height))
            canvas.alpha_composite(layer, (int(round(pos[0])), int(round(pos[1]))))

        # 卡片圆角 + 描边
        if tpl.corner_radius > 0.5:
            mask = rounded_mask((w, h), tpl.corner_radius)
            canvas.putalpha(Image.composite(canvas.getchannel("A"),
                                            Image.new("L", (w, h), 0), mask))
        if tpl.border_width > 0.5:
            d = ImageDraw.Draw(canvas)
            r = max(0.0, min(tpl.corner_radius, min(w, h) / 2))
            bw = float(tpl.border_width)
            half = bw / 2
            box = [half, half, w - 1 - half, h - 1 - half]
            if r > 0.5:
                d.rounded_rectangle(box, radius=max(0.0, r - half), outline=color_to_rgba(tpl.border_color), width=max(1, int(round(bw))))
            else:
                d.rectangle(box, outline=color_to_rgba(tpl.border_color), width=max(1, int(round(bw))))
        return canvas

    # ---- 文本 ----
    def _resolve_font(self, el: TextElement) -> Tuple[str, bool, bool]:
        fam = el.font_path if (el.font_path and os.path.isfile(el.font_path)) else el.font_family
        path, synth_bold, synth_italic = fontlib.resolve_variant(fam, el.bold, el.italic)
        if not path:
            path = fontlib.default_font_path()
        return path or "", synth_bold, synth_italic

    def _layout_geometry(self, el: TextElement, content: str, path: str, size: int,
                         box_w: float, box_h: float) -> Dict[str, Any]:
        """计算某个字号下的排版几何。"""
        font = get_font(path, size)
        line_h = max(1.0, size * max(0.5, float(el.line_spacing or 1.2)))
        nat = font.getmetrics()
        nat_h = float(sum(nat))
        geo: Dict[str, Any] = {
            "size": size, "font": font, "line_h": line_h, "nat_h": nat_h,
            "vertical": bool(el.vertical), "columns": [], "lines": [],
            "widths": [], "block_w": 0.0, "block_h": 0.0,
        }
        if el.vertical:
            per_col = 0
            if box_h > 0 and el.wrap:
                per_col = max(1, int(box_h // line_h))
            columns: List[List[str]] = []
            for para in content.split("\n"):
                cs = list(para)
                if per_col <= 0:
                    columns.append(cs)
                elif not cs:
                    columns.append([])
                else:
                    for i in range(0, len(cs), per_col):
                        columns.append(cs[i:i + per_col])
            gap = max(2.0, size * 0.10)
            widths = []
            for col in columns:
                w = max([measure_text(font, c, el.letter_spacing, path, size) for c in col] or [0.0])
                widths.append(max(w, size * 0.35))
            block_w = sum(widths) + gap * max(0, len(widths) - 1)
            max_chars = max([len(c) for c in columns] or [0])
            geo.update(columns=columns, widths=widths, gap=gap,
                       block_w=block_w, block_h=max_chars * line_h)
            return geo

        lines = self._wrap_lines(el, content, font, box_w, size, path)
        widths = [measure_text(font, ln, el.letter_spacing, path, size) for ln in lines]
        block_w = max(widths or [0.0])
        n = len(lines)
        block_h = ((n - 1) * line_h + nat_h) if n else 0.0
        geo.update(lines=lines, widths=widths, gap=0.0,
                   block_w=block_w, block_h=block_h)
        return geo

    def _render_text(self, el: TextElement, row: Dict[str, Any]) -> Tuple[Optional[Image.Image], Tuple[float, float]]:
        content = render_string(el.content, row)
        if el.text_transform == "upper":
            content = content.upper()
        elif el.text_transform == "lower":
            content = content.lower()
        elif el.text_transform == "title":
            content = content.title()
        if content == "":
            return None, (el.x, el.y)

        path, synth_bold, synth_italic = self._resolve_font(el)
        box_w = float(el.width or 0)
        box_h = float(el.height or 0)

        # ---- 自动缩小字号直到塞进文本框 ----
        cur = max(1, int(round(el.font_size)))
        geo = self._layout_geometry(el, content, path, cur, box_w, box_h)
        if el.auto_shrink:
            floor = max(4, int(round(el.min_font_size)))
            while cur > floor:
                over_w = box_w > 0 and geo["block_w"] > box_w + 0.5
                over_h = box_h > 0 and geo["block_h"] > box_h + 0.5
                if not (over_w or over_h):
                    break
                ratio = 1.0
                if over_w:
                    ratio = min(ratio, (box_w + 0.5) / max(1.0, geo["block_w"]))
                if over_h:
                    ratio = min(ratio, (box_h + 0.5) / max(1.0, geo["block_h"]))
                nxt = max(floor, min(cur - 1, int(cur * max(0.55, ratio))))
                if nxt >= cur:
                    nxt = cur - 1
                if nxt < floor:
                    break
                cur = nxt
                geo = self._layout_geometry(el, content, path, cur, box_w, box_h)

        size = geo["size"]
        font = geo["font"]
        line_h = geo["line_h"]
        nat_h = geo["nat_h"]
        block_w = geo["block_w"]
        block_h = geo["block_h"]
        ascent, descent = font.getmetrics()

        align = el.align if el.align in ("left", "center", "right") else "left"
        valign = el.valign if el.valign in ("top", "middle", "bottom") else "top"
        bw = box_w if box_w > 0 else block_w
        bh = box_h if box_h > 0 else block_h

        # 文本块在框内的左上角偏移
        if align == "center":
            base_x = (bw - block_w) / 2.0
        elif align == "right":
            base_x = bw - block_w
        else:
            base_x = 0.0
        if valign == "middle":
            base_y = (bh - block_h) / 2.0
        elif valign == "bottom":
            base_y = bh - block_h
        else:
            base_y = 0.0

        def place(text_w: float, idx: int):
            """返回第 idx 行/列的 x 偏移（行内对齐）。"""
            if el.vertical:
                x = base_x + sum(geo["widths"][:idx]) + geo["gap"] * idx
                x += max(0.0, (geo["widths"][idx] - text_w) / 2.0)
                return x
            if align == "center":
                return base_x + (block_w - text_w) / 2.0
            if align == "right":
                return base_x + (block_w - text_w)
            return base_x

        pad = int(max(10.0, size * 0.7,
                      float(el.stroke_width or 0) * 2 + 6,
                      (abs(float(el.shadow_offset)) + 6) if el.shadow else 0,
                      (size * 0.05) if synth_bold else 0))
        bw = box_w if box_w > 0 else block_w
        bh = box_h if box_h > 0 else block_h
        # 图层必须同时容纳整个文本框与文字内容（内容在框内偏移 base_x/base_y）
        need_w = max(bw, base_x + block_w)
        need_h = max(bh, base_y + block_h)
        extra = int(0.21 * (need_h + pad * 2)) + 6 if synth_italic else 0
        layer_w = int(need_w + pad * 2) + extra + 4
        layer_h = int(need_h + pad * 2) + 4
        layer = Image.new("RGBA", (max(1, layer_w), max(1, layer_h)), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)

        fill = color_to_rgba(el.color, 255)
        stroke_w = float(el.stroke_width or 0)
        sp = float(el.letter_spacing or 0)

        def draw_all(ox_shift: float, oy_shift: float, body_fill, extra_stroke: float,
                     stroke_col=None) -> None:
            col = stroke_col if stroke_col is not None else body_fill
            if el.vertical:
                for ci, colchars in enumerate(geo["columns"]):
                    for ri, ch in enumerate(colchars):
                        lw = measure_text(font, ch, sp, path, size)
                        x = pad + place(lw, ci)
                        y = pad + base_y + ri * line_h
                        d.text((x + ox_shift, y + oy_shift), ch, font=font,
                               fill=body_fill, anchor="la",
                               stroke_width=int(round(extra_stroke)), stroke_fill=col)
                return
            for i, ln in enumerate(geo["lines"]):
                if not ln:
                    continue
                lw = geo["widths"][i]
                x = pad + place(lw, i)
                y = pad + base_y + i * line_h
                if sp:
                    self._draw_spaced(d, (x + ox_shift, y + oy_shift), ln, font,
                                      body_fill, extra_stroke, col, sp)
                else:
                    d.text((x + ox_shift, y + oy_shift), ln, font=font,
                           fill=body_fill, anchor="la",
                           stroke_width=int(round(extra_stroke)), stroke_fill=col)

        # 1) 文字底色（先画，垫在文字下面；随斜体一起被剪切，形成斜切底块）
        if el.bg_color:
            md = ImageDraw.Draw(layer)
            pad_bg = float(el.bg_padding or 0)
            bx0 = pad + base_x - pad_bg
            by0 = pad + base_y - pad_bg
            bx1 = pad + base_x + block_w + pad_bg
            by1 = pad + base_y + block_h + pad_bg
            r = max(0.0, float(el.bg_radius or 0))
            box = [bx0, by0, bx1, by1]
            if r > 0.5:
                md.rounded_rectangle(box, radius=r, fill=color_to_rgba(el.bg_color))
            else:
                md.rectangle(box, fill=color_to_rgba(el.bg_color))
        # 2) 投影
        if el.shadow:
            so = max(1.0, float(el.shadow_offset))
            draw_all(so, so, color_to_rgba(el.shadow_color, 170), 0, None)
        # 3) 用户描边
        if stroke_w > 0:
            sc = color_to_rgba(el.stroke_color, 255)
            draw_all(0, 0, sc, stroke_w, sc)
        # 4) 文字本体（合成加粗靠同色描边模拟）
        bold_extra = max(1.0, round(size * 0.034)) if synth_bold else 0.0
        draw_all(0, 0, fill, bold_extra, fill if synth_bold else None)

        # 5) 合成斜体
        if synth_italic:
            layer = _shear_italic(layer, 0.21)

        return self._finalize(layer, el.x - pad, el.y - pad, el.rotation)

    @staticmethod
    def _finalize(layer: Image.Image, anchor_x: float, anchor_y: float,
                  rotation: float) -> Tuple[Image.Image, Tuple[float, float]]:
        """围绕图层中心旋转，返回最终图层与贴图位置。"""
        if abs(rotation) > 0.01:
            w, h = layer.size
            layer = layer.rotate(-rotation, resample=Image.BICUBIC, expand=True)
            w2, h2 = layer.size
            anchor_x -= (w2 - w) / 2.0
            anchor_y -= (h2 - h) / 2.0
        return layer, (anchor_x, anchor_y)

    def _wrap_lines(self, el: TextElement, content: str, font: ImageFont.FreeTypeFont,
                    box_w: float, size: int, path: str) -> List[str]:
        if el.wrap and box_w > 0:
            lines = wrap_text(content, box_w, font, el.letter_spacing, path, size)
        else:
            lines = content.split("\n")
        if el.max_lines and el.max_lines > 0 and len(lines) > el.max_lines:
            lines = lines[: el.max_lines]
            if el.ellipsis:
                cap = box_w if box_w > 0 else float("inf")
                if cap != float("inf"):
                    lines[-1] = _ellipsize(lines[-1], cap, font, el.letter_spacing, path, size)
                else:
                    lines[-1] = lines[-1] + "…"
        elif not el.wrap and box_w > 0:
            # 不换行但给了宽度：超宽的行做省略处理
            out = []
            for ln in lines:
                if measure_text(font, ln, el.letter_spacing, path, size) > box_w and el.ellipsis:
                    out.append(_ellipsize(ln, box_w, font, el.letter_spacing, path, size))
                else:
                    out.append(ln)
            lines = out
        return lines

    def _draw_spaced(self, d: ImageDraw.ImageDraw, xy, text: str,
                     font: ImageFont.FreeTypeFont, fill, stroke_width: float,
                     stroke_fill, spacing: float) -> None:
        x, y = xy
        for ch in text:
            d.text((x, y), ch, font=font, fill=fill, anchor="la",
                   stroke_width=int(round(stroke_width)),
                   stroke_fill=stroke_fill if stroke_fill is not None else fill)
            try:
                x += float(font.getlength(ch)) + spacing
            except Exception:
                x += font.size + spacing

    # ---- 图片 ----
    def _render_image(self, el: ImageElement, row: Dict[str, Any]) -> Tuple[Optional[Image.Image], Tuple[float, float]]:
        raw = el.source or ""
        path = render_string(raw, row)
        if path == raw and raw and not PLACEHOLDER_RE.search(raw):
            # 允许直接写列名
            if raw in row:
                path = str(row.get(raw) or "")
        path = (path or "").strip().strip('"')

        w = max(1.0, float(el.width or 0))
        h = max(1.0, float(el.height or 0))
        img = self._load_image(path) if path else None

        if img is None:
            layer = self._placeholder(el, w, h)
            return layer, (el.x, el.y)

        if el.grayscale:
            img = ImageOps.grayscale(img).convert("RGBA")

        box_w = int(round(w * max(0.05, el.zoom))) if el.zoom != 1 else int(round(w))
        box_h = int(round(h * max(0.05, el.zoom))) if el.zoom != 1 else int(round(h))
        layer = self._fit(img, (box_w, box_h), el.fit)

        if abs(el.offset_x) > 0.01 or abs(el.offset_y) > 0.01:
            ox, oy = int(round(el.offset_x)), int(round(el.offset_y))
            shifted = Image.new("RGBA", (layer.width + abs(ox) + 2, layer.height + abs(oy) + 2),
                                (0, 0, 0, 0))
            shifted.alpha_composite(layer, (max(0, ox), max(0, oy)))
            layer = shifted

        if el.corner_radius > 0.5:
            layer.putalpha(Image.composite(layer.getchannel("A"),
                                           Image.new("L", layer.size, 0),
                                           rounded_mask(layer.size, el.corner_radius)))
        if el.border_width > 0.5:
            d = ImageDraw.Draw(layer)
            bw = max(1, int(round(el.border_width)))
            r = max(0.0, float(el.corner_radius))
            box = [bw / 2, bw / 2, layer.width - 1 - bw / 2, layer.height - 1 - bw / 2]
            if r > 0.5:
                d.rounded_rectangle(box, radius=max(0.0, r - bw / 2),
                                    outline=color_to_rgba(el.border_color), width=bw)
            else:
                d.rectangle(box, outline=color_to_rgba(el.border_color), width=bw)
        return self._finalize(layer, el.x, el.y, el.rotation)

    def _fit(self, img: Image.Image, box: Tuple[int, int], fit: str) -> Image.Image:
        bw, bh = max(1, box[0]), max(1, box[1])
        if fit == "original":
            out = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
            src = img.copy()
            out.alpha_composite(src, (0, 0))
            return out
        if fit == "stretch":
            return img.resize((bw, bh), Image.LANCZOS)
        iw, ih = img.size
        if iw <= 0 or ih <= 0:
            return Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        scale = max(bw / iw, bh / ih) if fit == "cover" else min(bw / iw, bh / ih)
        nw, nh = max(1, int(round(iw * scale))), max(1, int(round(ih * scale)))
        resized = img.resize((nw, nh), Image.LANCZOS)
        out = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        out.alpha_composite(resized, ((bw - nw) // 2, (bh - nh) // 2))
        return out

    def _placeholder(self, el: ImageElement, w: float, h: float) -> Image.Image:
        bw, bh = max(1, int(round(w))), max(1, int(round(h)))
        layer = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        d.rounded_rectangle([1, 1, bw - 2, bh - 2], radius=min(12, min(bw, bh) // 6),
                            fill=(238, 238, 242, 255), outline=(200, 200, 210, 255), width=2)
        txt = el.placeholder_text or "未找到图片"
        fsize = max(12, min(bw, bh) // 8)
        f = get_font(fontlib.default_font_path(), fsize)
        try:
            tw = f.getlength(txt)
            th = sum(f.getmetrics())
            d.text(((bw - tw) / 2, (bh - th) / 2), txt, font=f, fill=(150, 150, 160, 255))
        except Exception:
            pass
        return layer

    # ---- 仅计算（用于适配预览） ----
    def render_thumbnail(self, tpl: CardTemplate, row: Optional[Dict[str, Any]],
                         max_size: Tuple[int, int]) -> Image.Image:
        img = self.render(tpl, row)
        img.thumbnail(max_size, Image.LANCZOS)
        return img
