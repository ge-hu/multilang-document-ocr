from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def resource_root() -> Path:
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root)
    return Path(__file__).resolve().parent.parent


def resource_path(*parts: str) -> Path:
    return resource_root().joinpath(*parts)


def find_tesseract() -> Path | None:
    configured = os.environ.get("MULTILANG_OCR_TESSERACT")
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured))
    candidates.extend(
        [
            resource_path("tesseract", "tesseract.exe"),
            resource_path("tesseract", "tesseract"),
        ]
    )
    system_binary = shutil.which("tesseract")
    if system_binary:
        candidates.append(Path(system_binary))
    if sys.platform == "win32":
        for variable in ("ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(variable)
            if base:
                candidates.append(Path(base) / "Tesseract-OCR" / "tesseract.exe")
    return next((path for path in candidates if path.is_file()), None)


def find_tessdata(tesseract: Path | None = None) -> Path | None:
    configured = os.environ.get("TESSDATA_PREFIX")
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured))
    candidates.append(resource_path("tesseract", "tessdata"))
    if tesseract:
        candidates.append(tesseract.parent / "tessdata")
    candidates.extend(
        [
            Path("/usr/share/tesseract-ocr/5/tessdata"),
            Path("/usr/share/tesseract-ocr/4.00/tessdata"),
            Path("/usr/share/tessdata"),
            Path("/usr/local/share/tessdata"),
        ]
    )
    return next((path for path in candidates if path.is_dir()), None)


def available_language_codes(tessdata: Path | None) -> set[str]:
    if not tessdata:
        return set()
    return {path.stem for path in tessdata.glob("*.traineddata")}


def hidden_subprocess_kwargs() -> dict[str, object]:
    if sys.platform != "win32":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return {"startupinfo": startupinfo, "creationflags": subprocess.CREATE_NO_WINDOW}
