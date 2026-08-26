from __future__ import annotations

import re
import subprocess
import tempfile
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from PIL import Image, ImageOps
from pypdf import PdfReader
import pypdfium2 as pdfium

from .runtime import (
    available_language_codes,
    find_tessdata,
    find_tesseract,
    hidden_subprocess_kwargs,
)


SUPPORTED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}


class OCRCancelled(RuntimeError):
    pass


class OCRConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class OCRSettings:
    language_codes: tuple[str, ...]
    dpi: int = 250
    force_ocr: bool = False
    page_segmentation_mode: int = 3


@dataclass(frozen=True)
class ExtractedPage:
    source: Path
    page_number: int
    page_count: int
    text: str
    method: str


ProgressCallback = Callable[[int, int, str], None]


def clean_text(value: str) -> str:
    value = unicodedata.normalize("NFC", value.replace("\x0c", ""))
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = "".join(ch for ch in value if ch in "\n\t" or unicodedata.category(ch) != "Cc")
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n{4,}", "\n\n\n", value)
    return value.strip()


def has_useful_text(value: str) -> bool:
    meaningful = sum(1 for char in value if char.isalnum())
    return meaningful >= 20


def count_pages(path: Path) -> int:
    if path.suffix.lower() == ".pdf":
        document = pdfium.PdfDocument(str(path))
        try:
            return max(1, len(document))
        finally:
            document.close()
    return 1


def _check_cancel(cancel_event: threading.Event | None) -> None:
    if cancel_event and cancel_event.is_set():
        raise OCRCancelled("用户已取消识别。")


def _run_tesseract(image: Image.Image, settings: OCRSettings) -> str:
    executable = find_tesseract()
    if not executable:
        raise OCRConfigurationError("未找到Tesseract OCR。请使用完整便携版，或安装Tesseract 5。")
    tessdata = find_tessdata(executable)
    available = available_language_codes(tessdata)
    missing = [code for code in settings.language_codes if code not in available]
    if missing:
        raise OCRConfigurationError("缺少OCR语言包：" + ", ".join(missing))
    if not settings.language_codes:
        raise OCRConfigurationError("请至少选择一种OCR语言。")

    with tempfile.TemporaryDirectory(prefix="multilang_ocr_") as folder:
        image_path = Path(folder) / "page.png"
        normalized = ImageOps.exif_transpose(image).convert("RGB")
        normalized.save(image_path, "PNG", optimize=False)
        command = [
            str(executable),
            str(image_path),
            "stdout",
            "-l",
            "+".join(settings.language_codes),
            "--oem",
            "1",
            "--psm",
            str(settings.page_segmentation_mode),
        ]
        if tessdata:
            command.extend(["--tessdata-dir", str(tessdata)])
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            **hidden_subprocess_kwargs(),
        )
        stdout = completed.stdout.decode("utf-8", errors="replace")
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        if completed.returncode != 0:
            raise RuntimeError(f"OCR执行失败（代码{completed.returncode}）：{stderr or '未知错误'}")
        return clean_text(stdout)


def _pdf_page_image(document: pdfium.PdfDocument, page_index: int, dpi: int) -> Image.Image:
    page = document[page_index]
    bitmap = None
    try:
        bitmap = page.render(scale=dpi / 72.0)
        return bitmap.to_pil().convert("RGB")
    finally:
        if bitmap is not None:
            bitmap.close()
        page.close()


def extract_paths(
    paths: Iterable[Path],
    settings: OCRSettings,
    progress: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> list[ExtractedPage]:
    normalized_paths = [Path(path) for path in paths]
    invalid = [str(path) for path in normalized_paths if path.suffix.lower() not in SUPPORTED_EXTENSIONS]
    if invalid:
        raise ValueError("暂不支持以下文件：" + ", ".join(invalid))

    total = sum(count_pages(path) for path in normalized_paths)
    completed_pages = 0
    results: list[ExtractedPage] = []

    for path in normalized_paths:
        _check_cancel(cancel_event)
        if path.suffix.lower() == ".pdf":
            reader = PdfReader(str(path))
            render_document = pdfium.PdfDocument(str(path))
            try:
                page_count = len(reader.pages)
                for page_index, page in enumerate(reader.pages):
                    _check_cancel(cancel_event)
                    page_number = page_index + 1
                    if progress:
                        progress(completed_pages, total, f"正在处理：{path.name} 第{page_number}/{page_count}页")
                    direct_text = clean_text(page.extract_text() or "")
                    if not settings.force_ocr and has_useful_text(direct_text):
                        text, method = direct_text, "文本层"
                    else:
                        text = _run_tesseract(_pdf_page_image(render_document, page_index, settings.dpi), settings)
                        method = "OCR"
                    results.append(ExtractedPage(path, page_number, page_count, text, method))
                    completed_pages += 1
                    if progress:
                        progress(completed_pages, total, f"已完成：{path.name} 第{page_number}页（{method}）")
            finally:
                render_document.close()
        else:
            if progress:
                progress(completed_pages, total, f"正在识别：{path.name}")
            with Image.open(path) as image:
                text = _run_tesseract(image, settings)
            results.append(ExtractedPage(path, 1, 1, text, "OCR"))
            completed_pages += 1
            if progress:
                progress(completed_pages, total, f"已完成：{path.name}")

    return results


def compose_text(pages: Iterable[ExtractedPage], include_page_labels: bool = False) -> str:
    blocks: list[str] = []
    previous_source: Path | None = None
    for page in pages:
        if include_page_labels:
            if page.source != previous_source:
                blocks.append(f"【{page.source.name}】")
            if page.page_count > 1:
                blocks.append(f"第 {page.page_number} 页")
        if page.text:
            blocks.append(page.text)
        previous_source = page.source
    return "\n\n".join(blocks).strip()
