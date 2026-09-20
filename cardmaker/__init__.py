"""卡片制作器 —— 用 Excel 批量生成卡片。

主要模块：
- model       卡片模板与元素数据模型
- renderer    Pillow 渲染引擎
- data_source Excel/CSV 读取与列绑定
- exporter    批量导出
- ui          Tkinter 可视化编辑界面
"""

__version__ = "1.0.0"
__all__ = ["model", "renderer", "data_source", "exporter", "fonts"]
