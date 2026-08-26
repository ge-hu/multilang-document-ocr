from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Iterable


WIDTH_AUTO = 0
WIDTH_FULL = 6
WIDTH_HALF = 3
WIDTH_THIRD = 2
VALID_WIDTH_UNITS = {WIDTH_AUTO, WIDTH_FULL, WIDTH_HALF, WIDTH_THIRD}


@dataclass
class LayoutBlock:
    """一个可独立排版和编辑的文字内容块。"""

    block_id: str
    text: str
    width_units: int = WIDTH_AUTO
    free_page: int = 0
    free_x_mm: float | None = None
    free_y_mm: float | None = None
    free_w_mm: float | None = None
    free_h_mm: float | None = None

    @classmethod
    def create(cls, text: str = "", width_units: int = WIDTH_AUTO) -> "LayoutBlock":
        return cls(f"block-{uuid.uuid4().hex}", text.strip(), width_units)

    def clone(self) -> "LayoutBlock":
        return LayoutBlock(
            block_id=self.block_id,
            text=self.text,
            width_units=self.width_units,
            free_page=self.free_page,
            free_x_mm=self.free_x_mm,
            free_y_mm=self.free_y_mm,
            free_w_mm=self.free_w_mm,
            free_h_mm=self.free_h_mm,
        )


@dataclass(frozen=True)
class LayoutSettings:
    font_size: float = 5.5
    margin_mm: float = 8.0
    layout_mode: str = "compact"
    columns: int = 0
    column_gap_mm: float = 3.0
    block_gap_mm: float = 1.8
    show_borders: bool = False

    def normalized(self) -> "LayoutSettings":
        mode = self.layout_mode if self.layout_mode in {"compact", "grid", "free"} else "compact"
        return LayoutSettings(
            font_size=min(18.0, max(4.0, float(self.font_size))),
            margin_mm=min(25.0, max(5.0, float(self.margin_mm))),
            layout_mode=mode,
            columns=0 if int(self.columns) == 0 else min(3, max(1, int(self.columns))),
            column_gap_mm=min(12.0, max(0.5, float(self.column_gap_mm))),
            block_gap_mm=min(12.0, max(0.0, float(self.block_gap_mm))),
            show_borders=bool(self.show_borders),
        )


def clean_layout_text(value: str) -> str:
    value = unicodedata.normalize("NFC", value.replace("\x0c", ""))
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = "".join(ch for ch in value if ch in "\n\t" or unicodedata.category(ch) != "Cc")
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


_LIST_START = re.compile(
    r"^\s*(?:\d{1,3}[.)、]|[A-Za-z][.)]|[-•●▪◆◇]|\[[A-Za-z]{2,5}\]|"
    r"(?:warning|caution|notice|注意|警告|提示)\b)",
    re.IGNORECASE,
)
_ENDS_SENTENCE = re.compile(r"[.!?。！？:：;；]$|[。！？][\"'”’）)]?$")


def reflow_ocr_text(value: str) -> str:
    """合并OCR保留下来的视觉换行，同时保留标题、列表和空行。"""

    cleaned = clean_layout_text(value)
    if not cleaned:
        return ""
    paragraphs = re.split(r"\n\s*\n", cleaned)
    result: list[str] = []
    for paragraph in paragraphs:
        lines = [line.strip() for line in paragraph.splitlines() if line.strip()]
        if not lines:
            continue
        merged: list[str] = []
        current = lines[0]
        for line in lines[1:]:
            preserve = bool(_LIST_START.match(line) or _ENDS_SENTENCE.search(current))
            if preserve:
                merged.append(current)
                current = line
            elif current.endswith("-") and line[:1].islower():
                current = current[:-1] + line
            else:
                separator = "" if _is_cjk_boundary(current[-1:], line[:1]) else " "
                current += separator + line
        merged.append(current)
        result.append("\n".join(merged))
    return "\n\n".join(result)


def _is_cjk_boundary(left: str, right: str) -> bool:
    def is_cjk(char: str) -> bool:
        return bool(char) and (
            "\u3400" <= char <= "\u9fff"
            or "\u3040" <= char <= "\u30ff"
            or "\uac00" <= char <= "\ud7af"
        )

    return is_cjk(left) and is_cjk(right)


def split_text_into_blocks(
    value: str,
    *,
    reflow: bool = False,
    default_width_units: int = WIDTH_AUTO,
) -> list[LayoutBlock]:
    cleaned = reflow_ocr_text(value) if reflow else clean_layout_text(value)
    if not cleaned:
        return []
    width_units = default_width_units if default_width_units in VALID_WIDTH_UNITS else WIDTH_AUTO
    return [
        LayoutBlock.create(part, width_units)
        for part in re.split(r"\n\s*\n", cleaned)
        if part.strip()
    ]


def blocks_to_text(blocks: Iterable[LayoutBlock]) -> str:
    return "\n\n".join(block.text.strip() for block in blocks if block.text.strip()).strip()


def block_summary(block: LayoutBlock, limit: int = 56) -> str:
    summary = re.sub(r"\s+", " ", block.text).strip() or "（空白文字框）"
    return summary if len(summary) <= limit else summary[: limit - 1] + "…"
