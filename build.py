"""把卡片制作器打包成 Windows 可执行程序（exe）。

用法（在项目目录）：
    python build.py              # 目录版（启动快，推荐）
    python build.py --onefile    # 单文件版（一个 exe，启动稍慢）
    python build.py --console    # 保留控制台窗口，便于排查问题

产物在 dist/ 目录下。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP_NAME = "卡片制作器"
ICON = os.path.join(HERE, "assets", "icon.ico")


def make_icon() -> str:
    """没有图标时生成一个简单的占位图标。"""
    if os.path.isfile(ICON):
        return ICON
    try:
        from PIL import Image, ImageDraw

        os.makedirs(os.path.dirname(ICON), exist_ok=True)
        img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([16, 8, 240, 248], radius=28, fill=(47, 127, 214, 255))
        d.rounded_rectangle([30, 22, 226, 234], radius=20, fill=(255, 255, 255, 255))
        d.rectangle([52, 62, 204, 96], fill=(192, 57, 43, 255))
        d.rectangle([52, 120, 204, 132], fill=(120, 120, 130, 255))
        d.rectangle([52, 150, 170, 162], fill=(120, 120, 130, 255))
        d.rectangle([52, 180, 188, 192], fill=(120, 120, 130, 255))
        img.save(ICON, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
        return ICON
    except Exception:
        return ""


def main() -> int:
    args = [a for a in sys.argv[1:]]
    onefile = "--onefile" in args
    console = "--console" in args
    args = [a for a in args if not a.startswith("--")]

    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
           "--windowed" if not console else "--console",
           "--name", APP_NAME]
    cmd.append("--onedir" if not onefile else "--onefile")
    icon = make_icon()
    if icon:
        cmd += ["--icon", icon]
    readme = os.path.join(HERE, "README.md")
    if os.path.isfile(readme):
        cmd += ["--add-data", f"{readme}{os.pathsep}."]
    fonts_dir = os.path.join(HERE, "fonts")
    if os.path.isdir(fonts_dir):
        cmd += ["--add-data", f"{fonts_dir}{os.pathsep}fonts"]
    cmd.append(os.path.join(HERE, "main.py"))

    print(">", " ".join(cmd))
    subprocess.check_call(cmd, cwd=HERE)
    print("\n打包完成！")
    if onefile:
        print("  可执行文件：", os.path.join(HERE, "dist", f"{APP_NAME}.exe"))
    else:
        print("  可执行文件：", os.path.join(HERE, "dist", APP_NAME, f"{APP_NAME}.exe"))
        print("  （整个文件夹都可拷贝到其他 Windows 电脑使用）")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as e:
        print("打包失败，退出码：", e.returncode)
        sys.exit(e.returncode)
    except KeyboardInterrupt:
        sys.exit(130)
