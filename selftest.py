"""自检脚本：不启动界面，直接验证渲染 / Excel 绑定 / 导出。"""
from __future__ import annotations

import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from cardmaker import samples  # noqa: E402
from cardmaker.data_source import (  # noqa: E402
    apply_row, base_columns, load_table, override_columns, split_column,
)
from cardmaker.exporter import export_cards, export_pdf, build_name  # noqa: E402
from cardmaker.model import default_template  # noqa: E402
from cardmaker.renderer import CardRenderer  # noqa: E402


def main() -> int:
    print("=" * 62)
    print("列名解析自检")
    for col in ["标题", "标题.x", "标题_x", "标题.size", "标题.bold", "标题.italic",
                "描述.color", "联系_人", "A-B", "图片.fit"]:
        print(f"  {col:<14} -> {split_column(col)}")

    print("=" * 62)
    print("生成示例数据")
    xlsx, img_dir = samples.generate(HERE)
    print("  ", xlsx)
    table = load_table(xlsx)
    print(f"   sheet={table.sheet} 行数={table.row_count}")
    print(f"   列：{table.headers}")
    print(f"   内容列：{base_columns(table.headers)}")
    print(f"   覆盖列：{override_columns(table.headers)}")

    tpl = default_template()
    rd = CardRenderer()
    out_dir = os.path.join(HERE, "output", "自检")
    os.makedirs(out_dir, exist_ok=True)

    print("=" * 62)
    print("逐行渲染 + 导出 PNG")
    done, errors = export_cards(
        tpl, table, out_dir, fmt="png",
        name_pattern="{index:03d}_{标题}", renderer=rd,
        progress=lambda d, t, m: (print(f"   {m}") or True) if d % 2 == 0 else True,
    )
    print(f"   导出 {done} 张，错误 {len(errors)} 条")
    for e in errors[:3]:
        print("   !", e)

    print("=" * 62)
    print("覆盖列生效检查（第 1 行标题应为红色）")
    row = table.rows[0]
    item = apply_row(tpl, row)
    t = item.element_by_name("标题")
    print(f"   标题.color={t.color}  描述.size={item.element_by_name('描述').font_size}")
    print(f"   文件名样例：{build_name('{index:03d}_{标题}', 1, row)}")

    print("=" * 62)
    print("导出多页 PDF")
    pdf = os.path.join(out_dir, "全部卡片.pdf")
    n, errs = export_pdf(tpl, table, pdf, renderer=rd)
    print(f"   {n} 页 -> {pdf}  错误 {len(errs)}")

    print("=" * 62)
    print("单张预览（第 3 行，竖排 + 斜体 + 描边 + 底色 组合压测）")
    from cardmaker.model import ImageElement, TextElement
    stress = default_template()
    stress.elements = [
        TextElement(name="斜体标题", content="斜体 Italic 测试", x=60, y=40, width=620,
                    font_size=56, bold=True, italic=True, align="center",
                    color="#FFFFFF", bg_color="#2F7FD6", bg_radius=12,
                    bg_padding=14, stroke_width=2, stroke_color="#123"),
        TextElement(name="竖排", content="竖排文字测试", x=560, y=200, width=120,
                    height=500, vertical=True, font_size=42, color="#333333"),
        TextElement(name="长文本", content="自动换行与省略号测试。" * 4, x=60, y=700,
                    width=520, height=140, font_size=24, max_lines=4,
                    ellipsis=True, shadow=True, shadow_offset=3),
        ImageElement(name="图", source=os.path.join(img_dir, "star.png"),
                     x=120, y=200, width=320, height=320, corner_radius=160,
                     border_width=6, border_color="#4A3C8C"),
    ]
    p = os.path.join(out_dir, "压测样例.png")
    rd.render(stress, {}).save(p)
    print("   ->", p)

    print("=" * 62)
    print("完成，输出目录：", out_dir)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
