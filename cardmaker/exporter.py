"""批量导出：逐行渲染成 PNG/JPG/WEBP，或合成多页 PDF。"""
from __future__ import annotations

import os
import re
import threading
import traceback
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from PIL import Image

from .data_source import DataTable, apply_row, split_column
from .model import CardTemplate
from .renderer import PLACEHOLDER_RE, CardRenderer, render_string

INVALID_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def sanitize_filename(name: str, max_len: int = 90) -> str:
    s = INVALID_CHARS.sub("_", str(name or "")).strip().strip(".")
    s = re.sub(r"\s+", " ", s)
    if not s:
        s = "card"
    return s[:max_len]


def build_name(pattern: str, index: int, row: Dict[str, Any], sheet: str = "") -> str:
    """文件名模板：支持 {index} {index:03d} {row} {sheet} 与 {{列名}}。"""
    text = pattern or "{index}"

    def col_sub(m: "re.Match[str]") -> str:
        v = row.get(m.group(1))
        return "" if v is None else str(v)

    text = PLACEHOLDER_RE.sub(col_sub, text)
    row_no = index

    def brace_sub(m: "re.Match[str]") -> str:
        body = m.group(1)
        key, _, fmt = body.partition(":")
        key = key.strip()
        if key == "index":
            try:
                return format(index, fmt) if fmt else str(index)
            except Exception:
                return str(index)
        if key == "row":
            try:
                return format(row_no, fmt) if fmt else str(row_no)
            except Exception:
                return str(row_no)
        if key == "sheet":
            return sheet
        v = row.get(key)
        return "" if v is None else str(v)

    text = re.sub(r"(?<!\{)\{([^{}]+)\}(?!\})", brace_sub, text)
    return sanitize_filename(text)


class ExportError(Exception):
    pass


def export_cards(
    tpl: CardTemplate,
    table: DataTable,
    out_dir: str,
    fmt: str = "png",
    name_pattern: str = "{index:03d}_{标题}",
    quality: int = 95,
    dpi: Optional[int] = None,
    start_index: int = 1,
    renderer: Optional[CardRenderer] = None,
    progress: Optional[Callable[[int, int, str], bool]] = None,
) -> Tuple[int, List[str]]:
    """逐行导出为图片。progress(done, total, message) 返回 False 表示请求中止。"""
    os.makedirs(out_dir, exist_ok=True)
    rd = renderer or CardRenderer()
    fmt = (fmt or "png").lower().lstrip(".")
    if fmt == "jpg":
        fmt = "jpeg"
    total = table.row_count
    done = 0
    errors: List[str] = []
    used_names: Dict[str, int] = {}

    for i, row in enumerate(table.rows):
        if progress and not progress(done, total, f"正在生成第 {i + 1}/{total} 张"):
            break
        try:
            item = apply_row(tpl, row)
            img = rd.render(item, row)
            base = build_name(name_pattern, start_index + i, row, table.sheet)
            if base in used_names:
                used_names[base] += 1
                base = f"{base}_{used_names[base]}"
            else:
                used_names[base] = 0
            ext = "jpg" if fmt == "jpeg" else fmt
            path = os.path.join(out_dir, f"{base}.{ext}")
            if fmt == "jpeg":
                bg = Image.new("RGB", img.size, (255, 255, 255))
                bg.paste(img, mask=img.getchannel("A"))
                bg.save(path, "JPEG", quality=int(quality), dpi=(dpi or tpl.dpi, dpi or tpl.dpi),
                        subsampling=0)
            elif fmt == "png":
                save_kwargs: Dict[str, Any] = {"dpi": (dpi or tpl.dpi, dpi or tpl.dpi)}
                img.save(path, "PNG", **save_kwargs)
            elif fmt == "webp":
                img.save(path, "WEBP", quality=int(quality))
            elif fmt == "bmp":
                img.convert("RGB").save(path, "BMP")
            elif fmt == "pdf":
                bg = Image.new("RGB", img.size, (255, 255, 255))
                bg.paste(img, mask=img.getchannel("A"))
                bg.save(path, "PDF", resolution=float(dpi or tpl.dpi))
            else:
                img.save(path)
            done += 1
        except Exception as e:
            errors.append(f"第 {i + 1} 行：{e}\n{traceback.format_exc(limit=2)}")
    return done, errors


def export_pdf(
    tpl: CardTemplate,
    table: DataTable,
    out_path: str,
    renderer: Optional[CardRenderer] = None,
    progress: Optional[Callable[[int, int, str], bool]] = None,
) -> Tuple[int, List[str]]:
    """把每一行渲染成一页，导出为单个 PDF。"""
    rd = renderer or CardRenderer()
    pages: List[Image.Image] = []
    errors: List[str] = []
    total = table.row_count
    for i, row in enumerate(table.rows):
        if progress and not progress(i, total, f"正在渲染第 {i + 1}/{total} 页"):
            break
        try:
            item = apply_row(tpl, row)
            img = rd.render(item, row).convert("RGB")
            pages.append(img)
        except Exception as e:
            errors.append(f"第 {i + 1} 行：{e}")
    if not pages:
        raise ExportError("没有任何可导出的页面")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    first, rest = pages[0], pages[1:]
    first.save(out_path, "PDF", save_all=True, append_images=rest,
               resolution=float(tpl.dpi or 300))
    for p in pages:
        p.close()
    return len(pages), errors


class AsyncExporter(threading.Thread):
    """后台导出线程，避免阻塞 UI。"""

    def __init__(self, fn: Callable[..., Any], on_done: Callable[[Any], None],
                 on_error: Callable[[Exception], None]):
        super().__init__(daemon=True)
        self._fn = fn
        self._on_done = on_done
        self._on_error = on_error

    def run(self) -> None:
        try:
            result = self._fn()
        except Exception as e:  # noqa: BLE001
            self._on_error(e)
            return
        self._on_done(result)
