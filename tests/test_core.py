from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader
from reportlab.pdfgen import canvas

from multilang_ocr.ocr_engine import OCRSettings, compose_text, extract_paths
from multilang_ocr.pdf_export import export_a4_pdf


class CoreTests(unittest.TestCase):
    def test_extracts_existing_pdf_text_without_ocr(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.pdf"
            document = canvas.Canvas(str(source))
            document.drawString(72, 760, "Existing searchable PDF text for direct extraction.")
            document.save()

            pages = extract_paths([source], OCRSettings(("eng",)))
            self.assertEqual(len(pages), 1)
            self.assertEqual(pages[0].method, "文本层")
            self.assertIn("Existing searchable PDF", pages[0].text)

    def test_export_is_a4_and_continuous(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "output.pdf"
            text = ("Română: Instrucțiuni. Ελληνικά: Οδηγίες. Български: Инструкции.\n\n" * 160).strip()
            export_a4_pdf(text, output, font_size=5.5, margin_mm=8)
            document = PdfReader(str(output))
            self.assertGreaterEqual(len(document.pages), 1)
            first_page = document.pages[0]
            self.assertAlmostEqual(float(first_page.mediabox.width), 595.28, delta=1)
            self.assertAlmostEqual(float(first_page.mediabox.height), 841.89, delta=1)
            extracted = "".join(page.extract_text() or "" for page in document.pages)
            self.assertIn("Română", extracted)

    def test_compose_text_does_not_force_page_breaks(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "two-pages.pdf"
            document = canvas.Canvas(str(source))
            for value in ("Page one searchable sentence.", "Page two searchable sentence."):
                document.drawString(72, 760, value)
                document.showPage()
            document.save()
            pages = extract_paths([source], OCRSettings(("eng",)))
            result = compose_text(pages, include_page_labels=False)
            self.assertNotIn("\x0c", result)
            self.assertIn("Page one", result)
            self.assertIn("Page two", result)


if __name__ == "__main__":
    unittest.main()
