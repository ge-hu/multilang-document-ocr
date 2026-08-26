from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader

from .languages import LANGUAGES
from .ocr_engine import OCRSettings, _run_tesseract
from .pdf_export import export_a4_pdf
from .runtime import available_language_codes, find_tessdata, find_tesseract, resource_path


def run_self_test() -> None:
    executable = find_tesseract()
    if not executable:
        raise RuntimeError("自检失败：未找到内置Tesseract。")
    tessdata = find_tessdata(executable)
    available = available_language_codes(tessdata)
    required = {item.code for item in LANGUAGES} if getattr(sys, "frozen", False) else {"eng"}
    missing = sorted(required - available)
    if missing:
        raise RuntimeError("自检失败：缺少语言包 " + ", ".join(missing))

    font_candidates = [
        resource_path("assets", "NotoSansSC-VF.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    font_path = next((path for path in font_candidates if path.is_file()), None)
    if not font_path:
        raise RuntimeError("自检失败：未找到测试字体。")

    image = Image.new("RGB", (960, 170), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(font_path), 42)
    draw.text((30, 48), "Safety instructions 5V 2A Type-C", font=font, fill="black")
    recognized = _run_tesseract(image, OCRSettings(("eng",), dpi=200))
    if "Safety" not in recognized or "5V" not in recognized or "2A" not in recognized:
        raise RuntimeError("自检失败：内置OCR未正确识别测试文本。")

    with tempfile.TemporaryDirectory(prefix="multilang_ocr_self_test_") as folder:
        output = Path(folder) / "a4-test.pdf"
        export_a4_pdf(
            "简体中文：安全说明\n\nRomână: Instrucțiuni\n\nБългарски: Инструкции",
            output,
            font_size=5.5,
            margin_mm=8.0,
        )
        document = PdfReader(str(output))
        if not output.is_file() or output.stat().st_size < 1000 or len(document.pages) != 1:
            raise RuntimeError("自检失败：A4 PDF导出异常。")

