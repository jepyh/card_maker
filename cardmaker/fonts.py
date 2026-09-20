"""字体解析：中文名 -> 字体文件，粗体/斜体变体，以及斜体合成。"""
from __future__ import annotations

import os
import sys
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

# 常见中英文字体名 -> Windows 字体文件名
FONT_FILES: Dict[str, str] = {
    "微软雅黑": "msyh.ttc", "microsoft yahei": "msyh.ttc", "msyh": "msyh.ttc",
    "微软雅黑 light": "msyhl.ttc",
    "黑体": "simhei.ttf", "simhei": "simhei.ttf", "simhei.ttf": "simhei.ttf",
    "宋体": "simsun.ttc", "simsun": "simsun.ttc",
    "新宋体": "simsun.ttc",
    "等线": "Deng.ttf", "dengxian": "Deng.ttf", "deng": "Deng.ttf",
    "arial": "arial.ttf", "arial.ttf": "arial.ttf",
    "times new roman": "times.ttf",
    "consolas": "consola.ttf",
    "courier new": "cour.ttf",
    "verdana": "verdana.ttf", "georgia": "georgia.ttf",
    "tahoma": "tahoma.ttf", "calibri": "calibri.ttf",
    "微软雅黑 bold": "msyhbd.ttc",
}

# 同族粗体/斜体变体
BOLD_MAP: Dict[str, str] = {
    "msyh.ttc": "msyhbd.ttc", "msyhl.ttc": "msyh.ttc",
    "arial.ttf": "arialbd.ttf", "times.ttf": "timesbd.ttf",
    "consola.ttf": "consolab.ttf", "cour.ttf": "courbd.ttf",
    "verdana.ttf": "verdanab.ttf", "georgia.ttf": "georgiab.ttf",
    "tahoma.ttf": "tahomabd.ttf", "calibri.ttf": "calibrib.ttf",
    "deng.ttf": "Dengb.ttf",
}
ITALIC_MAP: Dict[str, str] = {
    "arial.ttf": "ariali.ttf", "arialbd.ttf": "arialbi.ttf",
    "times.ttf": "timesi.ttf", "timesbd.ttf": "timesbi.ttf",
    "consola.ttf": "consolai.ttf", "cour.ttf": "couri.ttf",
    "verdana.ttf": "verdanai.ttf", "georgia.ttf": "georgiai.ttf",
    "tahoma.ttf": "tahoma.ttf", "calibri.ttf": "calibrii.ttf",
    "msyh.ttc": None, "simhei.ttf": None, "simsun.ttc": None,  # 中文无真斜体 -> 合成
}
ITALIC_BOLD_MAP: Dict[str, str] = {
    "arial.ttf": "arialbi.ttf", "times.ttf": "timesbi.ttf",
    "consola.ttf": "consolaz.ttf", "cour.ttf": "courbi.ttf",
    "verdana.ttf": "verdanaz.ttf", "georgia.ttf": "georgiaz.ttf",
}

DEFAULT_FAMILY = "微软雅黑"

_COMMON_FONT_DIRS = [
    os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts"),
    "/usr/share/fonts",
    "/System/Library/Fonts",
    os.path.expanduser("~/.fonts"),
]

# 找不到任何字体时的兜底候选（按优先级）
FALLBACK_CANDIDATES = [
    "msyh.ttc", "msyhbd.ttc", "simhei.ttf", "simsun.ttc", "Deng.ttf",
    "NotoSansCJK-Regular.ttc", "wqy-microhei.ttc", "arial.ttf", "DejaVuSans.ttf",
]


def font_dirs() -> List[str]:
    dirs = _COMMON_FONT_DIRS
    # 打包成 exe 后，随包携带的字体目录
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        dirs = [os.path.join(base, "fonts"), os.path.join(base, "assets", "fonts")] + list(dirs)
    else:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        dirs = [os.path.join(here, "fonts"), os.path.join(here, "assets", "fonts")] + list(dirs)
    return [d for d in dirs if d and os.path.isdir(d)]


@lru_cache(maxsize=1)
def list_font_files() -> List[str]:
    """扫描系统字体目录，返回可用字体文件名列表。"""
    names: List[str] = []
    for d in font_dirs():
        try:
            for fn in os.listdir(d):
                if fn.lower().endswith((".ttf", ".ttc", ".otf")):
                    names.append(fn)
        except OSError:
            continue
    return sorted(set(names), key=lambda s: s.lower())


@lru_cache(maxsize=1)
def font_index() -> Dict[str, str]:
    """{小写文件名: 完整路径}"""
    idx: Dict[str, str] = {}
    for d in font_dirs():
        try:
            for fn in os.listdir(d):
                if fn.lower().endswith((".ttf", ".ttc", ".otf")):
                    idx.setdefault(fn.lower(), os.path.join(d, fn))
        except OSError:
            continue
    return idx


def find_font_file(name: str) -> Optional[str]:
    """按字体文件名找完整路径。"""
    if not name:
        return None
    if os.path.isfile(name):
        return name
    idx = font_index()
    key = os.path.basename(name).lower()
    if key in idx:
        return idx[key]
    # 忽略扩展名匹配
    stem = os.path.splitext(key)[0]
    for k, v in idx.items():
        if os.path.splitext(k)[0] == stem:
            return v
    return None


def resolve_family(family: str) -> Optional[str]:
    """中英文家族名 -> 字体文件完整路径。"""
    if not family:
        family = DEFAULT_FAMILY
    if os.path.isfile(family):
        return family
    key = family.strip().lower()
    fname = FONT_FILES.get(key)
    if fname:
        p = find_font_file(fname)
        if p:
            return p
    # 直接当文件名试一次
    p = find_font_file(family)
    if p:
        return p
    return find_font_file(_default_file_name())


@lru_cache(maxsize=1)
def _default_file_name() -> str:
    for cand in FALLBACK_CANDIDATES:
        if find_font_file(cand):
            return cand
    files = list_font_files()
    return files[0] if files else ""


@lru_cache(maxsize=1)
def default_font_path() -> Optional[str]:
    """保证能拿到一个可用字体（含中文），用于兜底。"""
    p = resolve_family(DEFAULT_FAMILY)
    if p:
        return p
    fn = _default_file_name()
    return find_font_file(fn) if fn else None


def family_choices() -> List[str]:
    """UI 下拉框的字体候选：常用中文名 + 系统里真实存在的文件名。"""
    names = ["微软雅黑", "黑体", "宋体", "等线", "Arial", "Times New Roman", "Consolas"]
    return names


def resolve_variant(family: str, bold: bool, italic: bool) -> Tuple[Optional[str], bool, bool]:
    """返回 (字体文件路径, 是否需要合成加粗, 是否需要合成斜体)。

    中文黑体族没有真斜体文件，此时 italic 通过剪切变换合成。
    """
    path = resolve_family(family)
    if not path:
        return None, bold, italic
    base = os.path.basename(path).lower()

    need_bold = bold
    need_italic = italic

    if bold and italic:
        cand = ITALIC_BOLD_MAP.get(base)
        if cand:
            p = find_font_file(cand)
            if p:
                return p, False, False
        cand = BOLD_MAP.get(base)
        if cand:
            p = find_font_file(cand)
            if p:
                return p, False, True   # 粗体有真文件，斜体合成
        return path, True, True
    if bold:
        cand = BOLD_MAP.get(base)
        if cand:
            p = find_font_file(cand)
            if p:
                return p, False, False
        return path, True, False
    if italic:
        cand = ITALIC_MAP.get(base)
        if cand:
            p = find_font_file(cand)
            if p:
                return p, False, False
        return path, False, True
    return path, False, False


def file_to_family(path: str) -> str:
    """字体文件路径 -> 展示用的家族名（反查）。"""
    if not path:
        return DEFAULT_FAMILY
    base = os.path.basename(path)
    for k, v in FONT_FILES.items():
        if v.lower() == base.lower():
            return k.title() if k.isascii() else k
    stem = os.path.splitext(base)[0]
    return stem
