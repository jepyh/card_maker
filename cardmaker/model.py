"""卡片数据模型。

一个 CardTemplate 描述卡片的面板尺寸与全部元素；
元素分两类：文本元素（TextElement）与图片元素（ImageElement）。
所有几何单位均为像素，坐标原点在卡片左上角。
"""
from __future__ import annotations

import copy
import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

TEMPLATE_VERSION = 1

# ---------------------------------------------------------------- 单位换算
UNIT_ALIASES = {
    "px": "px", "像素": "px",
    "mm": "mm", "毫米": "mm",
    "cm": "cm", "厘米": "cm",
    "in": "in", "inch": "in", "英寸": "in",
}


def to_px(value: float, unit: str, dpi: int) -> float:
    """把带单位的长度换算成像素。"""
    u = UNIT_ALIASES.get((unit or "px").strip().lower(), "px")
    if u == "px":
        return float(value)
    if u == "mm":
        return float(value) * dpi / 25.4
    if u == "cm":
        return float(value) * dpi / 2.54
    if u == "in":
        return float(value) * dpi
    return float(value)


def from_px(value: float, unit: str, dpi: int) -> float:
    """把像素换算回指定单位。"""
    u = UNIT_ALIASES.get((unit or "px").strip().lower(), "px")
    if u == "px":
        return float(value)
    if u == "mm":
        return float(value) * 25.4 / dpi
    if u == "cm":
        return float(value) * 2.54 / dpi
    if u == "in":
        return float(value) / dpi
    return float(value)


# ---------------------------------------------------------------- 颜色工具
def normalize_color(value: Any, default: str = "#000000") -> str:
    """把任意颜色写法归一化成 #RRGGBB 或 #RRGGBBAA 字符串。"""
    if value is None:
        return default
    s = str(value).strip()
    if not s:
        return default
    if s.startswith("#"):
        s = s[1:]
    if s.lower().startswith("0x"):
        s = s[2:]
    # Excel/PIL 常见的 (R,G,B) 三元组字符串
    if s.startswith("(") and s.endswith(")"):
        s = s[1:-1]
    parts = [p for p in s.replace("，", ",").split(",") if p.strip() != ""]
    if len(parts) == 3 and all(p.strip().isdigit() for p in parts):
        r, g, b = (max(0, min(255, int(p))) for p in parts)
        return f"#{r:02X}{g:02X}{b:02X}"
    named = {
        "black": "#000000", "white": "#FFFFFF", "red": "#FF0000",
        "green": "#00A650", "blue": "#0000FF", "gray": "#808080",
        "grey": "#808080", "yellow": "#FFD400", "orange": "#FF8C00",
        "purple": "#800080", "gold": "#C9A227", "silver": "#C0C0C0",
        "黑色": "#000000", "白色": "#FFFFFF", "红色": "#FF0000",
        "绿色": "#00A650", "蓝色": "#0000FF", "灰色": "#808080",
        "金色": "#C9A227", "金色": "#C9A227",
    }
    if s.lower() in named:
        return named[s.lower()]
    if len(s) in (3, 4):
        s = "".join(ch * 2 for ch in s)
    if len(s) in (6, 8):
        try:
            int(s, 16)
            return "#" + s.upper()
        except ValueError:
            return default
    return default


def color_to_rgba(value: str, alpha: int = 255):
    """#RRGGBB / #RRGGBBAA -> (r, g, b, a)"""
    s = normalize_color(value, "#000000").lstrip("#")
    r = int(s[0:2], 16)
    g = int(s[2:4], 16)
    b = int(s[4:6], 16)
    a = int(s[6:8], 16) if len(s) == 8 else alpha
    return (r, g, b, a)


def truthy(value: Any, default: bool = False) -> bool:
    """把 Excel 里的各种写法解释成布尔值。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    s = str(value).strip().lower()
    if s == "":
        return default
    if s in ("1", "true", "yes", "y", "t", "是", "真", "开", "on", "✓", "√"):
        return True
    if s in ("0", "false", "no", "n", "f", "否", "假", "关", "off", "×", "x"):
        return False
    return default


def num(value: Any, default: float) -> float:
    if value is None:
        return float(default)
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace("，", "").replace(",", "")
    if s == "":
        return float(default)
    try:
        return float(s)
    except ValueError:
        return float(default)


def new_id() -> str:
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------- 元素
TEXT_ALIGNS = ["left", "center", "right"]
TEXT_VALIGNS = ["top", "middle", "bottom"]
IMAGE_FITS = ["contain", "cover", "stretch", "original"]


@dataclass
class Element:
    """元素基类。name 同时作为 Excel 列绑定的键名。"""

    name: str = "元素"
    type: str = "text"
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0          # 0 表示自动
    height: float = 0.0         # 0 表示自动
    rotation: float = 0.0       # 顺时针角度
    opacity: float = 1.0        # 0~1
    locked: bool = False
    visible: bool = True
    eid: str = field(default_factory=new_id)

    # ---- 序列化 ----
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Element":
        kind = d.get("type", "text")
        cls = TextElement if kind == "text" else ImageElement
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        kwargs = {k: v for k, v in d.items() if k in known}
        obj = cls(**kwargs)
        if not obj.eid:
            obj.eid = new_id()
        return obj


@dataclass
class TextElement(Element):
    type: str = "text"
    content: str = "文本"           # 支持 {{列名}} 占位符
    font_family: str = "微软雅黑"
    font_path: str = ""            # 指定字体文件时优先使用
    font_size: float = 32.0
    bold: bool = False
    italic: bool = False
    color: str = "#1A1A1A"
    align: str = "left"
    valign: str = "top"
    line_spacing: float = 1.25
    letter_spacing: float = 0.0
    wrap: bool = True
    max_lines: int = 0             # 0 = 不限制
    ellipsis: bool = True          # 超出 max_lines 时以 … 结尾
    auto_shrink: bool = True       # 放不下时自动缩小字号
    min_font_size: float = 8.0     # auto_shrink 的下限
    stroke_width: float = 0.0
    stroke_color: str = "#FFFFFF"
    shadow: bool = False
    shadow_offset: float = 2.0
    shadow_color: str = "#00000066"
    bg_color: str = ""             # 空 = 无底色
    bg_padding: float = 4.0
    bg_radius: float = 0.0
    vertical: bool = False         # 竖排文字
    text_transform: str = "none"   # none / upper / lower / title


@dataclass
class ImageElement(Element):
    type: str = "image"
    source: str = ""               # 本地文件路径，同样支持 {{列名}}
    fit: str = "cover"
    corner_radius: float = 0.0
    border_width: float = 0.0
    border_color: str = "#000000"
    bg_color: str = ""             # 空白填充色（图片比例不符时）
    placeholder_text: str = "未找到图片"
    grayscale: bool = False
    zoom: float = 1.0              # 在 fit 基础上再缩放
    offset_x: float = 0.0          # 裁剪偏移
    offset_y: float = 0.0


TEXT_FIELDS: List[Dict[str, Any]] = [
    # key, 中文名, 类型, 可选值/说明
    {"key": "name", "label": "元素名(列名)", "type": "str"},
    {"key": "content", "label": "文本内容", "type": "multiline"},
    {"key": "x", "label": "X 坐标", "type": "float"},
    {"key": "y", "label": "Y 坐标", "type": "float"},
    {"key": "width", "label": "文本框宽(0自动)", "type": "float"},
    {"key": "height", "label": "文本框高(0自动)", "type": "float"},
    {"key": "font_family", "label": "字体", "type": "font"},
    {"key": "font_size", "label": "字号", "type": "float"},
    {"key": "bold", "label": "加粗", "type": "bool"},
    {"key": "italic", "label": "斜体", "type": "bool"},
    {"key": "color", "label": "文字颜色", "type": "color"},
    {"key": "align", "label": "水平对齐", "type": "choice", "choices": TEXT_ALIGNS},
    {"key": "valign", "label": "垂直对齐", "type": "choice", "choices": TEXT_VALIGNS},
    {"key": "line_spacing", "label": "行距倍数", "type": "float"},
    {"key": "letter_spacing", "label": "字距(px)", "type": "float"},
    {"key": "wrap", "label": "自动换行", "type": "bool"},
    {"key": "max_lines", "label": "最多行数(0不限)", "type": "int"},
    {"key": "ellipsis", "label": "超行省略号", "type": "bool"},
    {"key": "auto_shrink", "label": "放不下自动缩小", "type": "bool"},
    {"key": "min_font_size", "label": "最小字号", "type": "float"},
    {"key": "stroke_width", "label": "描边宽度", "type": "float"},
    {"key": "stroke_color", "label": "描边颜色", "type": "color"},
    {"key": "shadow", "label": "投影", "type": "bool"},
    {"key": "shadow_offset", "label": "投影偏移", "type": "float"},
    {"key": "shadow_color", "label": "投影颜色", "type": "color"},
    {"key": "bg_color", "label": "文字底色", "type": "color_opt"},
    {"key": "bg_padding", "label": "底色留白", "type": "float"},
    {"key": "bg_radius", "label": "底色圆角", "type": "float"},
    {"key": "vertical", "label": "竖排文字", "type": "bool"},
    {"key": "text_transform", "label": "大小写转换", "type": "choice",
     "choices": ["none", "upper", "lower", "title"]},
    {"key": "rotation", "label": "旋转角度", "type": "float"},
    {"key": "opacity", "label": "不透明度", "type": "float"},
]

IMAGE_FIELDS: List[Dict[str, Any]] = [
    {"key": "name", "label": "元素名(列名)", "type": "str"},
    {"key": "source", "label": "图片路径/占位符", "type": "str"},
    {"key": "x", "label": "X 坐标", "type": "float"},
    {"key": "y", "label": "Y 坐标", "type": "float"},
    {"key": "width", "label": "宽", "type": "float"},
    {"key": "height", "label": "高", "type": "float"},
    {"key": "fit", "label": "填充方式", "type": "choice", "choices": IMAGE_FITS},
    {"key": "zoom", "label": "缩放系数", "type": "float"},
    {"key": "offset_x", "label": "水平偏移", "type": "float"},
    {"key": "offset_y", "label": "垂直偏移", "type": "float"},
    {"key": "corner_radius", "label": "圆角", "type": "float"},
    {"key": "border_width", "label": "边框宽度", "type": "float"},
    {"key": "border_color", "label": "边框颜色", "type": "color"},
    {"key": "bg_color", "label": "留白底色", "type": "color_opt"},
    {"key": "grayscale", "label": "灰度", "type": "bool"},
    {"key": "placeholder_text", "label": "缺图提示文字", "type": "str"},
    {"key": "rotation", "label": "旋转角度", "type": "float"},
    {"key": "opacity", "label": "不透明度", "type": "float"},
]


def fields_for(kind: str) -> List[Dict[str, Any]]:
    return TEXT_FIELDS if kind == "text" else IMAGE_FIELDS


# ---------------------------------------------------------------- 模板
CARD_PRESETS: List[Dict[str, Any]] = [
    {"name": "扑克牌 63×88mm", "w": 63, "h": 88, "unit": "mm"},
    {"name": "塔罗牌 70×120mm", "w": 70, "h": 120, "unit": "mm"},
    {"name": "三国杀 63×88mm", "w": 63, "h": 88, "unit": "mm"},
    {"name": "名片 90×54mm", "w": 90, "h": 54, "unit": "mm"},
    {"name": "方形 80×80mm", "w": 80, "h": 80, "unit": "mm"},
    {"name": "A6 明信片 148×105mm", "w": 148, "h": 105, "unit": "mm"},
    {"name": "小红书封面 1242×1660px", "w": 1242, "h": 1660, "unit": "px"},
    {"name": "教学卡片 800×600px", "w": 800, "h": 600, "unit": "px"},
]


@dataclass
class CardTemplate:
    name: str = "未命名模板"
    width: float = 744.0          # px
    height: float = 1039.0        # px
    dpi: int = 300
    background_color: str = "#FFFFFF"
    background_image: str = ""
    background_fit: str = "cover"
    corner_radius: float = 0.0
    border_width: float = 0.0
    border_color: str = "#000000"
    safe_margin: float = 24.0
    elements: List[Element] = field(default_factory=list)

    # ---- 便捷访问 ----
    def element_by_name(self, name: str) -> Optional[Element]:
        for e in self.elements:
            if e.name == name:
                return e
        return None

    def element_by_id(self, eid: str) -> Optional[Element]:
        for e in self.elements:
            if e.eid == eid:
                return e
        return None

    def add_element(self, el: Element) -> Element:
        base = el.name or ("文本" if el.type == "text" else "图片")
        existing = {e.name for e in self.elements}
        if base in existing:
            i = 2
            while f"{base}{i}" in existing:
                i += 1
            base = f"{base}{i}"
        el.name = base
        self.elements.append(el)
        return el

    def remove_element(self, eid: str) -> None:
        self.elements = [e for e in self.elements if e.eid != eid]

    def move_element(self, eid: str, delta: int) -> None:
        idx = next((i for i, e in enumerate(self.elements) if e.eid == eid), None)
        if idx is None:
            return
        new_idx = max(0, min(len(self.elements) - 1, idx + delta))
        if new_idx == idx:
            return
        el = self.elements.pop(idx)
        self.elements.insert(new_idx, el)

    def clone(self) -> "CardTemplate":
        return CardTemplate.from_dict(self.to_dict())

    # ---- 序列化 ----
    def to_dict(self) -> Dict[str, Any]:
        d = {
            "version": TEMPLATE_VERSION,
            "name": self.name,
            "width": self.width,
            "height": self.height,
            "dpi": self.dpi,
            "background_color": self.background_color,
            "background_image": self.background_image,
            "background_fit": self.background_fit,
            "corner_radius": self.corner_radius,
            "border_width": self.border_width,
            "border_color": self.border_color,
            "safe_margin": self.safe_margin,
            "elements": [e.to_dict() for e in self.elements],
        }
        return d

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "CardTemplate":
        t = CardTemplate(
            name=d.get("name", "未命名模板"),
            width=float(d.get("width", 744)),
            height=float(d.get("height", 1039)),
            dpi=int(d.get("dpi", 300)),
            background_color=normalize_color(d.get("background_color"), "#FFFFFF"),
            background_image=d.get("background_image", "") or "",
            background_fit=d.get("background_fit", "cover"),
            corner_radius=float(d.get("corner_radius", 0)),
            border_width=float(d.get("border_width", 0)),
            border_color=normalize_color(d.get("border_color"), "#000000"),
            safe_margin=float(d.get("safe_margin", 24)),
        )
        t.elements = [Element.from_dict(x) for x in d.get("elements", [])]
        return t

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @staticmethod
    def load(path: str) -> "CardTemplate":
        with open(path, "r", encoding="utf-8") as f:
            return CardTemplate.from_dict(json.load(f))


def default_template() -> CardTemplate:
    """新建模板时给出的示例卡面，方便用户直接看懂怎么用。"""
    t = CardTemplate(name="默认模板")
    t.elements = [
        TextElement(
            name="标题", content="{{标题}}", x=48, y=40, width=648, height=0,
            font_size=64, bold=True, align="center", color="#1F2C4C",
            line_spacing=1.15,
        ),
        TextElement(
            name="副标题", content="{{副标题}}", x=48, y=140, width=648,
            font_size=30, color="#C0392B", align="center",
        ),
        ImageElement(
            name="配图", source="{{图片}}", x=158, y=210, width=428, height=428,
            fit="cover", corner_radius=24, border_width=4, border_color="#1F2C4C",
        ),
        TextElement(
            name="描述", content="{{描述}}", x=64, y=680, width=616, height=300,
            font_size=26, color="#333333", align="left", line_spacing=1.4,
            auto_shrink=True,
        ),
        TextElement(
            name="页脚", content="{{页脚}}", x=48, y=975, width=648,
            font_size=20, color="#888888", align="center",
        ),
    ]
    return t
