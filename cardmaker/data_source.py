"""数据源：Excel / CSV 读取，以及「列 -> 元素属性」的绑定规则。

列名约定
--------
* ``标题``            -> 元素「标题」的正文内容（图片元素则是本地文件路径）
* ``标题.x`` ``标题.y`` -> 逐行覆盖该元素坐标（下划线 ``标题_x`` 同样识别）
* ``标题.size``       -> 覆盖字号；``标题.bold`` / ``标题.italic`` -> 覆盖加粗、斜体
* ``标题.color`` ``标题.font`` ``标题.align`` ``标题.valign`` ``标题.spacing``
* ``标题.w`` ``标题.h`` ``标题.rotate`` ``标题.opacity`` ``标题.fit`` ``标题.radius``
* 文本内容里还可以写 ``{{列名}}`` 占位符，渲染时替换成该行的值。
"""
from __future__ import annotations

import copy
import csv
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .model import CardTemplate, Element, ImageElement, TextElement, num, normalize_color, truthy

# 后缀 -> 元素属性名
SUFFIX_MAP: Dict[str, str] = {
    "x": "x", "left": "x", "左": "x",
    "y": "y", "top": "y", "上": "y",
    "w": "width", "width": "width", "宽": "width", "宽width": "width",
    "h": "height", "height": "height", "高": "height",
    "size": "font_size", "font_size": "font_size", "fontsize": "font_size", "字号": "font_size",
    "bold": "bold", "粗体": "bold", "加粗": "bold",
    "italic": "italic", "斜体": "italic",
    "color": "color", "colour": "color", "颜色": "color", "字体颜色": "color",
    "font": "font_family", "family": "font_family", "字体": "font_family",
    "align": "align", "对齐": "align", "水平对齐": "align",
    "valign": "valign", "垂直对齐": "valign",
    "spacing": "line_spacing", "line_spacing": "line_spacing", "行距": "line_spacing",
    "letter_spacing": "letter_spacing", "字距": "letter_spacing",
    "rotate": "rotation", "rotation": "rotation", "旋转": "rotation",
    "opacity": "opacity", "不透明度": "opacity", "透明": "opacity",
    "wrap": "wrap", "换行": "wrap",
    "maxlines": "max_lines", "max_lines": "max_lines", "最多行数": "max_lines",
    "src": "source", "source": "source", "path": "source", "路径": "source", "图片": "source",
    "fit": "fit", "填充": "fit",
    "zoom": "zoom", "缩放": "zoom",
    "radius": "corner_radius", "圆角": "corner_radius",
    "border": "border_width", "边框": "border_width",
    "border_color": "border_color", "边框颜色": "border_color",
    "visible": "visible", "显示": "visible",
    "grayscale": "grayscale", "灰度": "grayscale",
    "stroke": "stroke_width", "描边": "stroke_width",
    "stroke_color": "stroke_color", "描边颜色": "stroke_color",
    "shadow": "shadow", "投影": "shadow",
    "bg": "bg_color", "底色": "bg_color", "背景色": "bg_color",
    "content": "content", "文本": "content", "内容": "content",
}

_SEPARATORS = (".", "_", "-", "/")
BOOL_FIELDS = {"bold", "italic", "wrap", "visible", "grayscale", "shadow", "ellipsis",
               "auto_shrink", "vertical"}
INT_FIELDS = {"max_lines"}
COLOR_FIELDS = {"color", "stroke_color", "border_color", "shadow_color", "bg_color"}
FLOAT_FIELDS = {"x", "y", "width", "height", "font_size", "line_spacing", "letter_spacing",
                "rotation", "opacity", "corner_radius", "border_width", "zoom",
                "stroke_width", "shadow_offset", "bg_padding", "bg_radius", "min_font_size"}


def split_column(col: str) -> Tuple[str, Optional[str]]:
    """把 ``标题.size`` 拆成 ("标题", "font_size")；不是覆盖列则返回 (col, None)。"""
    if not col:
        return col, None
    raw = str(col).strip()
    for sep in _SEPARATORS:
        for i in range(len(raw) - 1, 0, -1):
            if raw[i] != sep:
                continue
            base, suffix = raw[:i].strip(), raw[i + 1:].strip().lower()
            suffix_key = suffix.replace(" ", "").replace("_", "")
            if suffix_key in SUFFIX_MAP:
                return base, SUFFIX_MAP[suffix_key]
    return raw, None


def base_columns(headers: Iterable[str]) -> List[str]:
    """取出所有「内容列」（不带属性后缀）。"""
    out: List[str] = []
    for h in headers:
        b, s = split_column(h)
        if s is None and str(h).strip():
            out.append(b)
    return out


def override_columns(headers: Iterable[str]) -> Dict[str, List[str]]:
    """{元素名: [覆盖列名, ...]}"""
    out: Dict[str, List[str]] = {}
    for h in headers:
        b, s = split_column(h)
        if s is not None:
            out.setdefault(b, []).append(str(h).strip())
    return out


def _coerce(field_name: str, value: Any) -> Any:
    if field_name in BOOL_FIELDS:
        return truthy(value, False)
    if field_name in INT_FIELDS:
        return int(num(value, 0))
    if field_name in COLOR_FIELDS:
        s = "" if value is None else str(value).strip()
        return normalize_color(s, "#000000") if s else ""
    if field_name in FLOAT_FIELDS:
        return num(value, 0.0)
    return "" if value is None else str(value)


def apply_row(tpl: CardTemplate, row: Dict[str, Any], inplace: bool = False) -> CardTemplate:
    """把一行数据里的覆盖列套用到模板上，返回用于本次渲染的模板副本。"""
    target = tpl if inplace else copy.deepcopy(tpl)
    for el in target.elements:
        for col, value in row.items():
            base, field_name = split_column(col)
            if field_name is None or base != el.name:
                continue
            if value is None:
                continue
            if isinstance(value, str) and value.strip() == "":
                continue
            try:
                setattr(el, field_name, _coerce(field_name, value))
            except Exception:
                continue
    return target


def row_columns_used(tpl: CardTemplate) -> List[str]:
    """模板里真正被引用到的列名（内容列 + 覆盖列）。"""
    from .renderer import used_columns

    cols: List[str] = []
    for el in tpl.elements:
        if isinstance(el, TextElement) and "{{" in (el.content or ""):
            cols += used_columns(el.content)
        if isinstance(el, ImageElement) and "{{" in (el.source or ""):
            cols += used_columns(el.source)
        cols.append(el.name)
    seen = set()
    out = []
    for c in cols:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


# ----------------------------------------------------------------- 数据表
@dataclass
class DataTable:
    path: str = ""
    sheet: str = ""
    headers: List[str] = field(default_factory=list)
    rows: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def preview(self, limit: int = 200) -> List[Dict[str, Any]]:
        return self.rows[:limit]

    def column_values(self, col: str) -> List[Any]:
        return [r.get(col) for r in self.rows]


def list_sheets(path: str) -> List[str]:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm", ".xltx", ".xltm"):
        try:
            from openpyxl import load_workbook
            wb = load_workbook(path, read_only=True, data_only=True)
            names = list(wb.sheetnames)
            wb.close()
            return names
        except Exception:
            return []
    if ext in (".csv", ".txt", ".tsv"):
        return ["CSV"]
    if ext == ".xls":
        return ["Sheet1"]
    return []


def load_table(path: str, sheet: Optional[str] = None, header_row: int = 1,
               max_rows: int = 0, skip_empty: bool = True) -> DataTable:
    """读取表格。header_row 是从 1 开始的表头行号。"""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".csv", ".txt", ".tsv"):
        return _load_csv(path, header_row, max_rows, skip_empty)
    if ext == ".xls":
        return _load_xls(path, header_row, max_rows, skip_empty)
    return _load_xlsx(path, sheet, header_row, max_rows, skip_empty)


def _clean_header(value: Any, idx: int) -> str:
    if value is None:
        return f"列{idx}"
    s = str(value).strip()
    return s if s else f"列{idx}"


def _rows_from_matrix(matrix: Sequence[Sequence[Any]], header_row: int,
                      max_rows: int, skip_empty: bool) -> Tuple[List[str], List[Dict[str, Any]]]:
    if not matrix:
        return [], []
    hi = max(0, min(len(matrix) - 1, header_row - 1))
    raw_headers = list(matrix[hi])
    headers: List[str] = []
    for i, h in enumerate(raw_headers):
        name = _clean_header(h, i + 1)
        # 重名自动去重
        if name in headers:
            k = 2
            while f"{name}_{k}" in headers:
                k += 1
            name = f"{name}_{k}"
        headers.append(name)

    rows: List[Dict[str, Any]] = []
    for raw in matrix[hi + 1:]:
        vals = list(raw) + [None] * max(0, len(headers) - len(raw))
        rec = {}
        for i, h in enumerate(headers):
            v = vals[i] if i < len(vals) else None
            if isinstance(v, str):
                v = v.strip()
                if v == "":
                    v = None
            rec[h] = v
        if skip_empty and all(v is None for v in rec.values()):
            continue
        rows.append(rec)
        if max_rows and len(rows) >= max_rows:
            break
    return headers, rows


def _load_xlsx(path: str, sheet: Optional[str], header_row: int,
               max_rows: int, skip_empty: bool) -> DataTable:
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        name = sheet if (sheet and sheet in wb.sheetnames) else wb.sheetnames[0]
        ws = wb[name]
        matrix = [list(r) for r in ws.iter_rows(values_only=True)]
    finally:
        wb.close()
    headers, rows = _rows_from_matrix(matrix, header_row, max_rows, skip_empty)
    return DataTable(path=path, sheet=name, headers=headers, rows=rows)


def _load_csv(path: str, header_row: int, max_rows: int, skip_empty: bool) -> DataTable:
    matrix: List[List[Any]] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except Exception:
            dialect = csv.excel
        for r in csv.reader(f, dialect):
            matrix.append(list(r))
    headers, rows = _rows_from_matrix(matrix, header_row, max_rows, skip_empty)
    return DataTable(path=path, sheet="CSV", headers=headers, rows=rows)


def _load_xls(path: str, header_row: int, max_rows: int, skip_empty: bool) -> DataTable:
    try:
        import xlrd  # type: ignore
    except ImportError as e:
        raise RuntimeError("读取 .xls 需要安装 xlrd：pip install xlrd") from e
    book = xlrd.open_workbook(path)
    sh = book.sheet_by_index(0)
    matrix = [[sh.cell_value(r, c) for c in range(sh.ncols)] for r in range(sh.nrows)]
    headers, rows = _rows_from_matrix(matrix, header_row, max_rows, skip_empty)
    return DataTable(path=path, sheet=sh.name, headers=headers, rows=rows)


# ----------------------------------------------------------------- 自动绑定
def suggest_bindings(tpl: CardTemplate, headers: Sequence[str]) -> List[Tuple[str, str]]:
    """给出 (元素名, 列名) 的建议绑定：优先同名，其次按顺序补位。"""
    out: List[Tuple[str, str]] = []
    content_cols = [c for c in base_columns(headers)]
    used = set()
    for el in tpl.elements:
        if el.name in content_cols:
            out.append((el.name, el.name))
            used.add(el.name)
    rest = [c for c in content_cols if c not in used]
    for el in tpl.elements:
        if el.name in used:
            continue
        if not rest:
            break
        out.append((el.name, rest.pop(0)))
        used.add(el.name)
    return out


def auto_fill_from_columns(tpl: CardTemplate, headers: Sequence[str],
                           start_y: float = 0.0) -> None:
    """把还没有被引用的列，自动生成为纵向排布的文本元素。"""
    bound = set()
    for el in tpl.elements:
        bound.add(el.name)
    content_cols = [c for c in base_columns(headers) if c not in bound]
    if not content_cols:
        return
    y = start_y or 40.0
    for col in content_cols:
        el = TextElement(
            name=col,
            content="{{%s}}" % col,
            x=48, y=y, width=max(100.0, tpl.width - 96),
            font_size=max(16.0, min(48.0, tpl.width / 16.0)),
            color="#222222", line_spacing=1.25,
        )
        tpl.elements.append(el)
        y += el.font_size * 2.6
