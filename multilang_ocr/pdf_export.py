from __future__ import annotations

import html
import re
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from .runtime import resource_path


def _contains_cjk(text: str) -> bool:
    return any("\u3400" <= char <= "\u9fff" for char in text)


def _register_font(text: str) -> str:
    candidates = [
        resource_path("assets", "NotoSansSC-VF.ttf"),
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for index, font_path in enumerate(candidates):
        if not font_path.is_file():
            continue
        font_name = f"MultilangOCRFont{index}"
        try:
            pdfmetrics.registerFont(TTFont(font_name, str(font_path)))
            return font_name
        except Exception:
            continue
    if _contains_cjk(text):
        font_name = "STSong-Light"
        try:
            pdfmetrics.getFont(font_name)
        except KeyError:
            pdfmetrics.registerFont(UnicodeCIDFont(font_name))
        return font_name
    return "Helvetica"


def _paragraph_markup(block: str) -> str:
    escaped = html.escape(block, quote=False)
    return escaped.replace("\n", "<br/>")


def export_a4_pdf(
    text: str,
    output_path: Path,
    font_size: float = 5.5,
    margin_mm: float = 8.0,
    title: str = "多语言文档提取结果",
) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cleaned = text.replace("\x0c", "").strip()
    font_name = _register_font(cleaned)
    leading = max(font_size + 1.0, font_size * 1.22)
    style = ParagraphStyle(
        "Body",
        fontName=font_name,
        fontSize=font_size,
        leading=leading,
        spaceBefore=0,
        spaceAfter=0,
        splitLongWords=True,
        allowWidows=True,
        allowOrphans=True,
        wordWrap="CJK" if _contains_cjk(cleaned) else None,
    )
    document = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=margin_mm * mm,
        rightMargin=margin_mm * mm,
        topMargin=margin_mm * mm,
        bottomMargin=margin_mm * mm,
        title=title,
        author="多语言文档OCR助手",
        allowSplitting=True,
    )
    story = []
    blocks = re.split(r"\n\s*\n", cleaned) if cleaned else [""]
    for index, block in enumerate(blocks):
        story.append(Paragraph(_paragraph_markup(block) or "&#160;", style))
        if index != len(blocks) - 1:
            story.append(Spacer(1, leading * 0.55))
    document.build(story)
    return output_path

