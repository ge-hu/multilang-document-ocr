from __future__ import annotations

import tkinter as tk

from main import OCRApp, TkinterDnD


def main() -> None:
    root = TkinterDnD.Tk() if TkinterDnD else tk.Tk()
    root.withdraw()
    app = OCRApp(root)
    assert app.start_button.cget("text") == "▶ 开始识别"
    assert app.start_button.master is app.top_toolbar
    assert app.start_button.winfo_manager() == "pack"
    app.layout_editor.load_text(
        "Polski: włączyć przycisk.\n\nTürkçe: Ürünün sıcaklığı.\n\n简体中文：安全说明。",
        reflow=False,
    )
    assert len(app.layout_editor.blocks) == 3
    app.layout_editor.actual_preview_size()
    assert app.layout_editor._preview_zoom_mode == "manual"
    assert app.layout_editor._preview_zoom == 1.0
    app.layout_editor.fit_preview_page()
    assert app.layout_editor._preview_zoom_mode == "fit"
    root.update_idletasks()
    app._on_close()


if __name__ == "__main__":
    main()
