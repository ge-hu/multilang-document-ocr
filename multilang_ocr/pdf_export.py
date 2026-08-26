from __future__ import annotations

import hashlib
import math
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from .layout import LayoutBlock, LayoutSettings, WIDTH_AUTO, WIDTH_FULL, blocks_to_text, split_text_into_blocks
from .runtime import resource_path


_FONT_LOCK = threading.Lock()


class MissingGlyphError(ValueError):
    pass


@dataclass(frozen=True)
class RenderedBox:
    block_id: str
    page_index: int
    x_pt: float
    y_pt: float
    width_pt: float
    height_pt: float
    fragment_index: int = 0


@dataclass(frozen=True)
class LayoutRenderResult:
    page_count: int
    boxes: tuple[RenderedBox, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class _PlacedFragment:
    box: RenderedBox
    lines: tuple[str, ...]


class _FontBundle:
    def __init__(self, text: str) -> None:
        self._width_cache: dict[tuple[str, float], float] = {}
        self.latin_name, self.latin_coverage = self._register_first(
            "Latin",
            (
                resource_path("assets", "DejaVuSans.ttf"),
                resource_path("assets", "NotoSans-Regular.ttf"),
                Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
                Path("C:/Windows/Fonts/arial.ttf"),
            ),
        )
        self.cjk_name, self.cjk_coverage = self._register_first(
            "CJK",
            (
                resource_path("assets", "NotoSansSC-Regular.ttf"),
                resource_path("assets", "NotoSansSC-VF.ttf"),
                Path("C:/Windows/Fonts/msyh.ttc"),
                Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
                Path("/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf"),
            ),
        )
        if self.latin_name is None:
            self.latin_name = "Helvetica"
            self.latin_coverage = set(range(32, 127))
        if self.cjk_name is None and any(_is_cjk(char) for char in text):
            with _FONT_LOCK:
                try:
                    pdfmetrics.getFont("STSong-Light")
                except KeyError:
                    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
            self.cjk_name = "STSong-Light"
            self.cjk_coverage = None

    @staticmethod
    def _register_first(prefix: str, candidates: Iterable[Path]) -> tuple[str | None, set[int] | None]:
        for path in candidates:
            if not path.is_file():
                continue
            digest = hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:10]
            name = f"MultilangOCR{prefix}{digest}"
            try:
                with _FONT_LOCK:
                    try:
                        font = pdfmetrics.getFont(name)
                    except KeyError:
                        pdfmetrics.registerFont(TTFont(name, str(path)))
                        font = pdfmetrics.getFont(name)
                cmap = getattr(getattr(font, "face", None), "charToGlyph", None)
                coverage = set(cmap) if cmap else None
                return name, coverage
            except Exception:
                continue
        return None, None

    def font_for(self, char: str) -> str:
        codepoint = ord(char)
        preferred = (
            ((self.cjk_name, self.cjk_coverage), (self.latin_name, self.latin_coverage))
            if _is_cjk(char)
            else ((self.latin_name, self.latin_coverage), (self.cjk_name, self.cjk_coverage))
        )
        for name, coverage in preferred:
            if name and (coverage is None or codepoint in coverage):
                return name
        raise MissingGlyphError(
            f"内置字体缺少字符：{char!r}（U+{codepoint:04X}）。请删除该异常字符或更新字体包后再导出。"
        )

    def validate(self, text: str) -> None:
        missing: list[str] = []
        seen: set[str] = set()
        for char in text:
            if char in "\n\r\t" or char.isspace():
                continue
            try:
                self.font_for(char)
            except MissingGlyphError:
                if char not in seen:
                    missing.append(f"{char} (U+{ord(char):04X})")
                    seen.add(char)
                if len(missing) >= 8:
                    break
        if missing:
            raise MissingGlyphError("内置字体无法显示以下字符：" + "、".join(missing))

    def width(self, value: str, font_size: float) -> float:
        total = 0.0
        for char in value.replace("\t", "    "):
            key = (char, font_size)
            width = self._width_cache.get(key)
            if width is None:
                width = pdfmetrics.stringWidth(char, self.font_for(char), font_size)
                self._width_cache[key] = width
            total += width
        return total


def _is_cjk(char: str) -> bool:
    if not char:
        return False
    codepoint = ord(char)
    return (
        0x2E80 <= codepoint <= 0x9FFF
        or 0xAC00 <= codepoint <= 0xD7AF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0xFF00 <= codepoint <= 0xFFEF
        or 0x20000 <= codepoint <= 0x2FA1F
    )


def _split_long_token(token: str, max_width: float, fonts: _FontBundle, font_size: float) -> list[str]:
    chunks: list[str] = []
    current = ""
    width = 0.0
    for char in token:
        char_width = fonts.width(char, font_size)
        if current and width + char_width > max_width:
            chunks.append(current)
            current = char
            width = char_width
        else:
            current += char
            width += char_width
    if current or not chunks:
        chunks.append(current)
    return chunks


def _wrap_text(text: str, max_width: float, fonts: _FontBundle, font_size: float) -> list[str]:
    max_width = max(8.0, max_width)
    wrapped: list[str] = []
    source_lines = text.replace("\t", "    ").split("\n") or [""]
    for source_line in source_lines:
        line = source_line.strip()
        if not line:
            wrapped.append("")
            continue
        words = line.split()
        current = ""
        for word in words:
            candidate = word if not current else current + " " + word
            if fonts.width(candidate, font_size) <= max_width:
                current = candidate
                continue
            if current:
                wrapped.append(current)
                current = ""
            if fonts.width(word, font_size) <= max_width:
                current = word
                continue
            chunks = _split_long_token(word, max_width, fonts, font_size)
            wrapped.extend(chunks[:-1])
            current = chunks[-1]
        wrapped.append(current)
    return wrapped or [""]


def _block_span(block: LayoutBlock, columns: int) -> int:
    if block.width_units == WIDTH_AUTO:
        length = len(block.text)
        if length > 1200:
            return columns
        if length > 480 and columns >= 3:
            return 2
        return 1
    return min(columns, max(1, int(round(columns * max(1, min(6, block.width_units)) / 6.0))))


def _content_geometry(settings: LayoutSettings) -> tuple[float, float, float, float, float, float]:
    page_width, page_height = A4
    margin = settings.margin_mm * mm
    content_width = page_width - 2 * margin
    content_height = page_height - 2 * margin
    return page_width, page_height, margin, content_width, content_height, page_height - margin


def _height_for_lines(line_count: int, leading: float, padding: float) -> float:
    return max(1, line_count) * leading + 2 * padding


def _make_fragment(
    block: LayoutBlock,
    page_index: int,
    x: float,
    y: float,
    width: float,
    lines: list[str],
    leading: float,
    padding: float,
    fragment_index: int,
) -> _PlacedFragment:
    height = _height_for_lines(len(lines), leading, padding)
    box = RenderedBox(block.block_id, page_index, x, y, width, height, fragment_index)
    return _PlacedFragment(box, tuple(lines))


def _compact_plan(
    blocks: list[LayoutBlock], settings: LayoutSettings, fonts: _FontBundle
) -> tuple[list[_PlacedFragment], int]:
    _, _, margin, content_width, content_height, bottom = _content_geometry(settings)
    columns = settings.columns
    column_gap = settings.column_gap_mm * mm
    block_gap = settings.block_gap_mm * mm
    column_width = (content_width - (columns - 1) * column_gap) / columns
    leading = max(settings.font_size + 1.0, settings.font_size * 1.24)
    padding = max(1.5, settings.font_size * 0.42)
    page_index = 0
    column_y = [margin] * columns
    placed: list[_PlacedFragment] = []

    for block in blocks:
        span = _block_span(block, columns)
        width = span * column_width + (span - 1) * column_gap
        lines = _wrap_text(block.text, width - 2 * padding, fonts, settings.font_size)
        remaining = list(lines)
        fragment_index = 0
        while remaining:
            full_height = _height_for_lines(len(remaining), leading, padding)
            candidates = []
            for start in range(columns - span + 1):
                top = max(column_y[start : start + span])
                candidates.append((top, start))
            fitting = [(top, start) for top, start in candidates if top + full_height <= bottom]
            if fitting:
                top, start = min(fitting)
                take = len(remaining)
            elif full_height <= content_height:
                page_index += 1
                column_y = [margin] * columns
                continue
            else:
                top, start = min(candidates)
                max_lines = int(math.floor((bottom - top - 2 * padding) / leading))
                if max_lines < 1:
                    page_index += 1
                    column_y = [margin] * columns
                    continue
                take = min(len(remaining), max_lines)
            chunk = remaining[:take]
            del remaining[:take]
            x = margin + start * (column_width + column_gap)
            fragment = _make_fragment(
                block, page_index, x, top, width, chunk, leading, padding, fragment_index
            )
            placed.append(fragment)
            new_y = top + fragment.box.height_pt + block_gap
            for column in range(start, start + span):
                column_y[column] = new_y
            fragment_index += 1
    return placed, max(1, page_index + 1)


def _grid_plan(
    blocks: list[LayoutBlock], settings: LayoutSettings, fonts: _FontBundle
) -> tuple[list[_PlacedFragment], int]:
    _, _, margin, content_width, content_height, bottom = _content_geometry(settings)
    columns = settings.columns
    column_gap = settings.column_gap_mm * mm
    block_gap = settings.block_gap_mm * mm
    column_width = (content_width - (columns - 1) * column_gap) / columns
    leading = max(settings.font_size + 1.0, settings.font_size * 1.24)
    padding = max(1.5, settings.font_size * 0.42)
    page_index = 0
    row_top = margin
    row_height = 0.0
    cursor = 0
    placed: list[_PlacedFragment] = []

    for block in blocks:
        span = _block_span(block, columns)
        if cursor + span > columns:
            row_top += row_height + block_gap
            row_height = 0.0
            cursor = 0
        width = span * column_width + (span - 1) * column_gap
        remaining = _wrap_text(block.text, width - 2 * padding, fonts, settings.font_size)
        fragment_index = 0
        while remaining:
            full_height = _height_for_lines(len(remaining), leading, padding)
            if row_top + full_height > bottom and row_height > 0:
                row_top += row_height + block_gap
                row_height = 0.0
                cursor = 0
            if cursor + span > columns:
                row_top += row_height + block_gap
                row_height = 0.0
                cursor = 0
            if row_top + full_height > bottom and full_height <= content_height:
                page_index += 1
                row_top = margin
                row_height = 0.0
                cursor = 0
                continue
            max_lines = int(math.floor((bottom - row_top - 2 * padding) / leading))
            if max_lines < 1:
                page_index += 1
                row_top = margin
                row_height = 0.0
                cursor = 0
                continue
            take = min(len(remaining), max_lines)
            chunk = remaining[:take]
            del remaining[:take]
            x = margin + cursor * (column_width + column_gap)
            fragment = _make_fragment(
                block, page_index, x, row_top, width, chunk, leading, padding, fragment_index
            )
            placed.append(fragment)
            row_height = max(row_height, fragment.box.height_pt)
            cursor += span
            fragment_index += 1
            if remaining:
                page_index += 1
                row_top = margin
                row_height = 0.0
                cursor = 0
    return placed, max(1, page_index + 1)


def _free_plan(
    blocks: list[LayoutBlock], settings: LayoutSettings, fonts: _FontBundle
) -> tuple[list[_PlacedFragment], int, list[str]]:
    page_width, _, margin, content_width, _, bottom = _content_geometry(settings)
    leading = max(settings.font_size + 1.0, settings.font_size * 1.24)
    padding = max(1.5, settings.font_size * 0.42)
    placed: list[_PlacedFragment] = []
    warnings: list[str] = []
    page_count = 1
    for index, block in enumerate(blocks):
        if None in (block.free_x_mm, block.free_y_mm, block.free_w_mm, block.free_h_mm):
            warnings.append(f"第{index + 1}个文字框尚未设置自由位置，已临时按整行显示。")
            x = margin
            y = margin + index * 14 * mm
            width = content_width
            configured_height = 12 * mm
            page_index = 0
        else:
            x = float(block.free_x_mm) * mm
            y = float(block.free_y_mm) * mm
            width = max(12 * mm, float(block.free_w_mm) * mm)
            configured_height = max(6 * mm, float(block.free_h_mm) * mm)
            page_index = max(0, int(block.free_page))
        lines = _wrap_text(block.text, width - 2 * padding, fonts, settings.font_size)
        required_height = _height_for_lines(len(lines), leading, padding)
        height = max(configured_height, required_height)
        if required_height > configured_height + 0.5:
            warnings.append(f"第{index + 1}个文字框高度不足，预览已自动展开。")
        if x < margin - 0.1 or x + width > page_width - margin + 0.1 or y < margin - 0.1 or y + height > bottom + 0.1:
            warnings.append(f"第{index + 1}个文字框超出A4可打印范围。")
        box = RenderedBox(block.block_id, page_index, x, y, width, height, 0)
        placed.append(_PlacedFragment(box, tuple(lines)))
        page_count = max(page_count, page_index + 1)

    for left_index, left in enumerate(placed):
        for right in placed[left_index + 1 :]:
            if left.box.page_index != right.box.page_index:
                continue
            if _boxes_overlap(left.box, right.box):
                warnings.append("自由排版中存在重叠文字框，请移动后再导出。")
                return placed, page_count, warnings
    return placed, page_count, warnings


def _boxes_overlap(left: RenderedBox, right: RenderedBox) -> bool:
    return not (
        left.x_pt + left.width_pt <= right.x_pt
        or right.x_pt + right.width_pt <= left.x_pt
        or left.y_pt + left.height_pt <= right.y_pt
        or right.y_pt + right.height_pt <= left.y_pt
    )


def _draw_line(pdf: canvas.Canvas, fonts: _FontBundle, x: float, baseline: float, line: str, font_size: float) -> None:
    cursor = x
    run_font: str | None = None
    run = ""
    for char in line:
        font_name = fonts.font_for(char)
        if run and font_name != run_font:
            pdf.setFont(run_font, font_size)
            pdf.drawString(cursor, baseline, run)
            cursor += fonts.width(run, font_size)
            run = ""
        run_font = font_name
        run += char
    if run:
        pdf.setFont(run_font, font_size)
        pdf.drawString(cursor, baseline, run)


def _draw_pdf(
    output_path: Path,
    fragments: list[_PlacedFragment],
    page_count: int,
    settings: LayoutSettings,
    fonts: _FontBundle,
    title: str,
) -> None:
    _, page_height = A4
    leading = max(settings.font_size + 1.0, settings.font_size * 1.24)
    padding = max(1.5, settings.font_size * 0.42)
    document = canvas.Canvas(str(output_path), pagesize=A4, pageCompression=1)
    document.setTitle(title)
    document.setAuthor("多语言文档OCR助手")
    document.setCreator("多语言文档OCR助手")
    for page_index in range(page_count):
        document.setFillColor(colors.black)
        for fragment in (item for item in fragments if item.box.page_index == page_index):
            box = fragment.box
            bottom_y = page_height - box.y_pt - box.height_pt
            if settings.show_borders:
                document.setStrokeColor(colors.HexColor("#A7AFBA"))
                document.setLineWidth(0.35)
                document.roundRect(box.x_pt, bottom_y, box.width_pt, box.height_pt, 1.5, stroke=1, fill=0)
            document.setFillColor(colors.black)
            baseline = page_height - box.y_pt - padding - settings.font_size
            for line in fragment.lines:
                if line:
                    _draw_line(document, fonts, box.x_pt + padding, baseline, line, settings.font_size)
                baseline -= leading
        document.showPage()
    document.save()


def export_layout_pdf(
    blocks: Iterable[LayoutBlock],
    output_path: Path,
    settings: LayoutSettings | None = None,
    title: str = "多语言文档提取结果",
    *,
    strict: bool = True,
) -> LayoutRenderResult:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    normalized_settings = (settings or LayoutSettings()).normalized()
    normalized_blocks = [block.clone() for block in blocks if block.text.strip()]
    if normalized_settings.columns == 0:
        normalized_settings = replace(
            normalized_settings,
            columns=_auto_columns(normalized_blocks),
        )
    text = blocks_to_text(normalized_blocks)
    fonts = _FontBundle(text)
    fonts.validate(text)

    warnings: list[str] = []
    if normalized_settings.layout_mode == "free":
        fragments, page_count, warnings = _free_plan(normalized_blocks, normalized_settings, fonts)
    elif normalized_settings.layout_mode == "grid":
        fragments, page_count = _grid_plan(normalized_blocks, normalized_settings, fonts)
    else:
        fragments, page_count = _compact_plan(normalized_blocks, normalized_settings, fonts)
    if strict and warnings:
        raise ValueError("\n".join(dict.fromkeys(warnings)))
    _draw_pdf(output_path, fragments, page_count, normalized_settings, fonts, title)
    return LayoutRenderResult(
        page_count=page_count,
        boxes=tuple(fragment.box for fragment in fragments),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def _auto_columns(blocks: list[LayoutBlock]) -> int:
    if not blocks:
        return 1
    lengths = sorted(len(block.text) for block in blocks)
    median = lengths[len(lengths) // 2]
    upper_quartile = lengths[min(len(lengths) - 1, int(len(lengths) * 0.75))]
    if len(blocks) >= 9 and median <= 260 and upper_quartile <= 520:
        return 3
    if len(blocks) >= 4 and median <= 900:
        return 2
    return 1


def export_a4_pdf(
    text: str,
    output_path: Path,
    font_size: float = 5.5,
    margin_mm: float = 8.0,
    title: str = "多语言文档提取结果",
) -> Path:
    blocks = split_text_into_blocks(text, default_width_units=WIDTH_FULL)
    settings = LayoutSettings(font_size=font_size, margin_mm=margin_mm, layout_mode="compact", columns=1)
    export_layout_pdf(blocks, output_path, settings, title)
    return Path(output_path)


def copy_settings(settings: LayoutSettings, **changes) -> LayoutSettings:
    return replace(settings, **changes)
