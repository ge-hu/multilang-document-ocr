from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader
from reportlab.pdfgen import canvas

from multilang_ocr.layout import (
    LayoutBlock,
    LayoutSettings,
    WIDTH_FULL,
    WIDTH_HALF,
    reflow_ocr_text,
    split_text_into_blocks,
)
from multilang_ocr.ocr_engine import OCRSettings, compose_text, extract_paths
from multilang_ocr.pdf_export import export_a4_pdf, export_layout_pdf


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

    def test_compact_layout_uses_both_columns(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "compact.pdf"
            blocks = [LayoutBlock.create(f"Language {index}: short instructions.", WIDTH_HALF) for index in range(8)]
            result = export_layout_pdf(
                blocks,
                output,
                LayoutSettings(font_size=5.5, margin_mm=8, layout_mode="compact", columns=2),
            )
            first_page_x = {round(box.x_pt, 1) for box in result.boxes if box.page_index == 0}
            self.assertGreaterEqual(len(first_page_x), 2)
            self.assertEqual(result.page_count, 1)

    def test_multilingual_fonts_cover_european_text(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "languages.pdf"
            text = (
                "Polski: Aby włączyć tryb grzewczy, przytrzymaj przycisk zasilania.\n\n"
                "Türkçe: Güç bankası dahil değildir. Ürünün sıcaklığı için aşağıdaki talimatlar.\n\n"
                "Română: Instrucțiuni și precauții.\n\n"
                "Български: Инструкции за безопасност.\n\n"
                "Ελληνικά: Οδηγίες ασφαλείας."
            )
            blocks = [LayoutBlock.create(part, WIDTH_FULL) for part in text.split("\n\n")]
            export_layout_pdf(blocks, output, LayoutSettings(columns=2))
            extracted = "".join(page.extract_text() or "" for page in PdfReader(str(output)).pages)
            self.assertIn("włączyć", extracted)
            self.assertIn("Ürünün", extracted)
            self.assertIn("Instrucțiuni", extracted)

    def test_free_layout_rejects_overlaps(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "overlap.pdf"
            left = LayoutBlock.create("First", WIDTH_HALF)
            right = LayoutBlock.create("Second", WIDTH_HALF)
            for block in (left, right):
                block.free_x_mm = 10
                block.free_y_mm = 10
                block.free_w_mm = 80
                block.free_h_mm = 20
            with self.assertRaisesRegex(ValueError, "重叠"):
                export_layout_pdf(
                    [left, right],
                    output,
                    LayoutSettings(layout_mode="free"),
                )

    def test_reflow_merges_visual_line_breaks_but_keeps_lists(self) -> None:
        source = "This sentence was wrapped in the\noriginal PDF without punctuation\n\n1. First step\n2. Second step"
        result = reflow_ocr_text(source)
        self.assertIn("the original PDF", result)
        self.assertIn("1. First step\n2. Second step", result)

    def test_language_grouping_merges_same_language_sentences(self) -> None:
        source = (
            "Aby włączyć tryb grzewczy, przytrzymaj przycisk zasilania.\n\n"
            "Wskaźnik zapali się na czerwono.\n\n"
            "Güç bankası dahil değildir.\n\n"
            "Yalnızca 5V 2A güç bankası veya adaptörü kullanın."
        )
        blocks = split_text_into_blocks(source, reflow=True, group_by_language=True)
        self.assertEqual(len(blocks), 2)
        self.assertIn("Wskaźnik", blocks[0].text)
        self.assertIn("Yalnızca", blocks[1].text)

    def test_language_grouping_detects_sections_without_blank_lines(self) -> None:
        source = (
            "Käyttöohje\nLangaton yhteys käyttäjille\nOdota yhteyden muodostumista.\n"
            "Εγχειρίδιο Χρήστη\nΑσύρματη σύνδεση για χρήστες\nΠεριμένετε τη σύνδεση.\n"
            "Lietotāja rokasgrāmata\nBezvadu savienojums lietotājiem\nLūdzu, uzgaidiet."
        )
        blocks = split_text_into_blocks(source, reflow=True, group_by_language=True)
        self.assertEqual(len(blocks), 3)
        self.assertIn("Käyttöohje", blocks[0].text)
        self.assertIn("Εγχειρίδιο", blocks[1].text)
        self.assertIn("Lietotāja", blocks[2].text)


if __name__ == "__main__":
    unittest.main()
