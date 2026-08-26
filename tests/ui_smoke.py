from __future__ import annotations

import tkinter as tk

from main import OCRApp, TkinterDnD


def main() -> None:
    root = TkinterDnD.Tk() if TkinterDnD else tk.Tk()
    root.withdraw()
    app = OCRApp(root)
    app.layout_editor.load_text(
        "Polski: włączyć przycisk.\n\nTürkçe: Ürünün sıcaklığı.\n\n简体中文：安全说明。",
        reflow=False,
    )
    root.update_idletasks()
    app._on_close()


if __name__ == "__main__":
    main()
