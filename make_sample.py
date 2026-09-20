"""生成示例数据：samples/示例卡片.xlsx 与 samples/images/*.png

用法：
    python make_sample.py
"""
from __future__ import annotations

import os

from cardmaker.samples import generate

if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    xlsx, img_dir = generate(here)
    print("已生成表格：", xlsx)
    print("已生成配图：", img_dir)
