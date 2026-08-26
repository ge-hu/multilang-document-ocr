from __future__ import annotations

import sys
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("用法：prepare_fonts.py 输入可变字体.ttf 输出常规字体.ttf")
    source = Path(sys.argv[1])
    output = Path(sys.argv[2])
    font = TTFont(source)
    if "fvar" not in font:
        raise RuntimeError("输入文件不是可变字体。")
    regular = instantiateVariableFont(font, {"wght": 400}, inplace=False)
    name_table = regular["name"]
    for platform_id, encoding_id, language_id in ((3, 1, 0x409), (1, 0, 0)):
        name_table.setName("Noto Sans SC", 1, platform_id, encoding_id, language_id)
        name_table.setName("Regular", 2, platform_id, encoding_id, language_id)
        name_table.setName("Noto Sans SC Regular", 4, platform_id, encoding_id, language_id)
        name_table.setName("NotoSansSC-Regular", 6, platform_id, encoding_id, language_id)
    output.parent.mkdir(parents=True, exist_ok=True)
    regular.save(output)
    check = TTFont(output)
    if "fvar" in check:
        raise RuntimeError("常规字体转换失败：输出文件仍包含可变轴。")


if __name__ == "__main__":
    main()
