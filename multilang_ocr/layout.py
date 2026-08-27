from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Iterable

from langdetect import DetectorFactory, detect_langs
from langdetect.lang_detect_exception import LangDetectException

from .languages import LANGUAGES


WIDTH_AUTO = 0
WIDTH_FULL = 6
WIDTH_HALF = 3
WIDTH_THIRD = 2
VALID_WIDTH_UNITS = {WIDTH_AUTO, WIDTH_FULL, WIDTH_HALF, WIDTH_THIRD}


# Tesseract uses mostly ISO-639-3 codes, while langdetect uses ISO-639-1.
_TESSERACT_TO_DETECTOR = {
    "eng": "en", "nld": "nl", "pol": "pl", "tur": "tr",
    "spa": "es", "fra": "fr", "dan": "da", "lit": "lt",
    "swe": "sv", "ron": "ro", "bul": "bg", "fin": "fi",
    "hrv": "hr", "lav": "lv", "ell": "el", "por": "pt",
    "est": "et", "deu": "de", "slv": "sl", "slk": "sk",
    "ita": "it", "ces": "cs", "hun": "hu", "chi_sim": "zh-cn",
}
_CODE_ALIASES = {
    **{code.upper(): language for code, language in _TESSERACT_TO_DETECTOR.items()},
    **{language.upper(): language for language in _TESSERACT_TO_DETECTOR.values()},
    **{language.upper().replace("-", "_"): language for language in _TESSERACT_TO_DETECTOR.values()},
    "ZH": "zh-cn",
    "CN": "zh-cn",
}
_BRACKETED_LANGUAGE_TAG = re.compile(r"^\s*[\[(]\s*([A-Za-z_\-]{2,7})\s*[\])]", re.IGNORECASE)
_PREFIX_LANGUAGE_TAG = re.compile(r"^\s*([A-Za-z_\-]{2,7})\s*[:：\-–—]", re.IGNORECASE)
_UPPER_LANGUAGE_TAG = re.compile(r"^\s*([A-Z]{2,3})(?=\s)")
_METADATA_LINE = re.compile(r"^\s*(?:【.*】|第\s*\d+\s*页)\s*$")
_LANGUAGE_NAME_PREFIXES = {
    name.casefold(): _TESSERACT_TO_DETECTOR[item.code]
    for item in LANGUAGES
    for name in (item.native_name, item.chinese_name)
}

DetectorFactory.seed = 0


@dataclass
class _LanguageUnit:
    text: str
    blank_before: bool
    language: str | None = None
    confidence: float = 0.0
    explicit: bool = False


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
    r"^\s*(?:\(\d{1,3}\)|\d{1,3}[.)、]|[A-Za-z][.)]|[-•●▪◆◇]|\[[A-Za-z]{2,5}\]|"
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


def _explicit_language_tag(line: str) -> str | None:
    folded = line.strip().casefold()
    for name, language in _LANGUAGE_NAME_PREFIXES.items():
        if folded == name or re.match(rf"^{re.escape(name)}\s*[:：\-–—]", folded):
            return language
    for pattern in (_BRACKETED_LANGUAGE_TAG, _PREFIX_LANGUAGE_TAG, _UPPER_LANGUAGE_TAG):
        match = pattern.match(line)
        if not match:
            continue
        alias = match.group(1).replace("-", "_").upper()
        language = _CODE_ALIASES.get(alias)
        if language:
            return language
    return None


def _language_guess(line: str) -> tuple[str | None, float, bool]:
    explicit = _explicit_language_tag(line)
    if explicit:
        return explicit, 1.0, True
    if _METADATA_LINE.match(line):
        return None, 0.0, False
    if sum(character.isalpha() for character in line) < 8:
        return None, 0.0, False
    try:
        probabilities = detect_langs(line)
    except LangDetectException:
        return None, 0.0, False
    if not probabilities:
        return None, 0.0, False
    best = probabilities[0]
    return best.lang, float(best.prob), False


def _nearest_strong_language(units: list[_LanguageUnit], index: int, direction: int) -> str | None:
    cursor = index + direction
    while 0 <= cursor < len(units):
        unit = units[cursor]
        if unit.explicit or unit.confidence >= 0.90:
            return unit.language
        cursor += direction
    return None


def _join_language_units(units: list[_LanguageUnit]) -> str:
    if not units:
        return ""
    result = units[0].text
    for unit in units[1:]:
        result += ("\n\n" if unit.blank_before else "\n") + unit.text
    return result.strip()


def group_text_by_language(value: str) -> list[str]:
    """Merge consecutive lines into one block per detected language."""

    cleaned = clean_layout_text(value)
    if not cleaned:
        return []

    units: list[_LanguageUnit] = []
    blank_before = False
    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if not line:
            blank_before = True
            continue
        language, confidence, explicit = _language_guess(line)
        units.append(_LanguageUnit(line, blank_before, language, confidence, explicit))
        blank_before = False
    if not units:
        return []

    # Technical specifications and short lines inherit the nearest stable body.
    for index, unit in enumerate(units):
        if unit.explicit or unit.confidence >= 0.90:
            continue
        previous = _nearest_strong_language(units, index, -1)
        following = _nearest_strong_language(units, index, 1)
        if previous == following and previous:
            unit.language = previous
        elif previous:
            unit.language = previous
        elif following:
            unit.language = following

    known = next((unit.language for unit in units if unit.language), None)
    current = known
    for unit in units:
        if unit.language:
            current = unit.language
        else:
            unit.language = current

    runs: list[list[_LanguageUnit]] = []
    for unit in units:
        if runs and runs[-1][0].language == unit.language:
            runs[-1].append(unit)
        else:
            runs.append([unit])

    # Fold a short A-B-A island back into its surrounding language.
    changed = True
    while changed and len(runs) >= 3:
        changed = False
        for index in range(1, len(runs) - 1):
            before, middle, after = runs[index - 1], runs[index], runs[index + 1]
            middle_chars = sum(character.isalpha() for unit in middle for character in unit.text)
            if (
                before[0].language == after[0].language
                and not any(unit.explicit for unit in middle)
                and (len(middle) <= 2 or middle_chars < 180)
            ):
                runs[index - 1 : index + 2] = [before + middle + after]
                changed = True
                break

    grouped: list[str] = []
    for run in runs:
        text = _join_language_units(run)
        if text:
            grouped.append(text)
    return grouped


def split_text_into_blocks(
    value: str,
    *,
    reflow: bool = False,
    group_by_language: bool = False,
    default_width_units: int = WIDTH_AUTO,
) -> list[LayoutBlock]:
    cleaned = clean_layout_text(value)
    if not cleaned:
        return []
    width_units = default_width_units if default_width_units in VALID_WIDTH_UNITS else WIDTH_AUTO
    if group_by_language:
        parts = group_text_by_language(cleaned)
    else:
        parts = [part for part in re.split(r"\n\s*\n", cleaned) if part.strip()]
    if reflow:
        parts = [reflow_ocr_text(part) for part in parts]
    return [
        LayoutBlock.create(part, width_units)
        for part in parts
        if part.strip()
    ]


def blocks_to_text(blocks: Iterable[LayoutBlock]) -> str:
    return "\n\n".join(block.text.strip() for block in blocks if block.text.strip()).strip()


def block_summary(block: LayoutBlock, limit: int = 56) -> str:
    summary = re.sub(r"\s+", " ", block.text).strip() or "（空白文字框）"
    return summary if len(summary) <= limit else summary[: limit - 1] + "…"
