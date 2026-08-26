from __future__ import annotations

import queue
import sys
import tempfile
import threading
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:  # 本地开发环境可不安装，按钮导入仍可使用。
    DND_FILES = None
    TkinterDnD = None

from multilang_ocr import __version__
from multilang_ocr.languages import COMMON, EUROPEAN_23, LANGUAGES
from multilang_ocr.ocr_engine import (
    OCRCancelled,
    OCRSettings,
    SUPPORTED_EXTENSIONS,
    compose_text,
    extract_paths,
)
from multilang_ocr.pdf_export import export_a4_pdf


APP_TITLE = f"多语言文档OCR助手 v{__version__}（试用版）"


class OCRApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.paths: list[Path] = []
        self.events: queue.Queue[tuple] = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.language_vars = {item.code: tk.BooleanVar(value=item.code in COMMON) for item in LANGUAGES}
        self.force_ocr = tk.BooleanVar(value=False)
        self.include_labels = tk.BooleanVar(value=False)
        self.dpi = tk.IntVar(value=250)
        self.font_size = tk.DoubleVar(value=5.5)
        self.margin_mm = tk.DoubleVar(value=8.0)
        self.status = tk.StringVar(value="请添加PDF、JPG或PNG文件。")
        self._build_ui()
        self.root.after(100, self._poll_events)

    def _build_ui(self) -> None:
        self.root.title(APP_TITLE)
        self.root.geometry("1120x790")
        self.root.minsize(920, 680)
        self.root.option_add("*Font", ("Microsoft YaHei UI", 9))

        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Accent.TButton", font=("Microsoft YaHei UI", 10, "bold"), padding=(12, 7))

        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)

        toolbar = ttk.Frame(outer)
        toolbar.pack(fill=tk.X)
        ttk.Button(toolbar, text="添加文件", command=self._choose_files).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="删除选中", command=self._remove_selected).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(toolbar, text="清空文件", command=self._clear_files).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Label(toolbar, text="支持 PDF / JPG / PNG；文件仅在本机处理", foreground="#166534").pack(side=tk.RIGHT)

        self.file_tree = ttk.Treeview(outer, columns=("type", "status"), show="headings", height=6, selectmode="extended")
        self.file_tree.heading("type", text="格式")
        self.file_tree.heading("status", text="文件")
        self.file_tree.column("type", width=75, anchor=tk.CENTER, stretch=False)
        self.file_tree.column("status", width=850, anchor=tk.W)
        self.file_tree.pack(fill=tk.X, pady=(8, 8))
        if DND_FILES:
            self.file_tree.drop_target_register(DND_FILES)
            self.file_tree.dnd_bind("<<Drop>>", self._on_drop)

        settings = ttk.Frame(outer)
        settings.pack(fill=tk.X)
        language_box = ttk.LabelFrame(settings, text="OCR识别语言（可多选）", padding=8)
        language_box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        actions = ttk.Frame(language_box)
        actions.pack(fill=tk.X, pady=(0, 5))
        ttk.Button(actions, text="常用：中英", command=lambda: self._select_languages(COMMON)).pack(side=tk.LEFT)
        ttk.Button(actions, text="欧洲23种", command=lambda: self._select_languages(EUROPEAN_23)).pack(side=tk.LEFT, padx=4)
        ttk.Button(actions, text="全部24种", command=lambda: self._select_languages(tuple(self.language_vars))).pack(side=tk.LEFT)
        ttk.Button(actions, text="清空", command=lambda: self._select_languages(())).pack(side=tk.LEFT, padx=4)
        ttk.Label(actions, text="选择越多，扫描件识别越慢", foreground="#9a3412").pack(side=tk.RIGHT)

        language_grid = ttk.Frame(language_box)
        language_grid.pack(fill=tk.X)
        for index, item in enumerate(LANGUAGES):
            row, column = divmod(index, 4)
            ttk.Checkbutton(
                language_grid,
                text=f"{item.chinese_name} ({item.code})",
                variable=self.language_vars[item.code],
            ).grid(row=row, column=column, sticky=tk.W, padx=(0, 12), pady=1)

        option_box = ttk.LabelFrame(settings, text="处理与排版", padding=10)
        option_box.pack(side=tk.LEFT, fill=tk.Y, padx=(8, 0))
        ttk.Checkbutton(option_box, text="强制OCR（忽略原PDF文字层）", variable=self.force_ocr).grid(row=0, column=0, columnspan=2, sticky=tk.W)
        ttk.Checkbutton(option_box, text="在结果中加入文件名和页码", variable=self.include_labels).grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=(3, 7))
        ttk.Label(option_box, text="扫描分辨率").grid(row=2, column=0, sticky=tk.W)
        ttk.Combobox(option_box, textvariable=self.dpi, values=(200, 250, 300), width=8, state="readonly").grid(row=2, column=1, sticky=tk.E)
        ttk.Label(option_box, text="PDF字号(pt)").grid(row=3, column=0, sticky=tk.W, pady=5)
        ttk.Spinbox(option_box, from_=4.0, to=14.0, increment=0.5, textvariable=self.font_size, width=8).grid(row=3, column=1, sticky=tk.E)
        ttk.Label(option_box, text="A4边距(mm)").grid(row=4, column=0, sticky=tk.W)
        ttk.Spinbox(option_box, from_=5.0, to=25.0, increment=0.5, textvariable=self.margin_mm, width=8).grid(row=4, column=1, sticky=tk.E)
        ttk.Label(option_box, text="连续排版，不按语言另起页", foreground="#166534").grid(row=5, column=0, columnspan=2, sticky=tk.W, pady=(8, 0))

        editor_box = ttk.LabelFrame(outer, text="提取结果（可直接修改）", padding=6)
        editor_box.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self.editor = tk.Text(editor_box, wrap=tk.WORD, undo=True, font=("Microsoft YaHei UI", 9))
        editor_scroll = ttk.Scrollbar(editor_box, orient=tk.VERTICAL, command=self.editor.yview)
        self.editor.configure(yscrollcommand=editor_scroll.set)
        editor_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.editor.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        bottom = ttk.Frame(outer)
        bottom.pack(fill=tk.X, pady=(8, 0))
        self.progress = ttk.Progressbar(bottom, mode="determinate", length=250)
        self.progress.pack(side=tk.LEFT)
        ttk.Label(bottom, textvariable=self.status).pack(side=tk.LEFT, padx=8)
        self.cancel_button = ttk.Button(bottom, text="取消", command=self._cancel, state=tk.DISABLED)
        self.cancel_button.pack(side=tk.RIGHT)
        self.start_button = ttk.Button(bottom, text="开始提取", style="Accent.TButton", command=self._start)
        self.start_button.pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(bottom, text="导出PDF", command=self._export_pdf).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(bottom, text="导出TXT", command=self._export_txt).pack(side=tk.RIGHT)

    def _choose_files(self) -> None:
        selected = filedialog.askopenfilenames(
            title="选择文档或图片",
            filetypes=[("支持的文件", "*.pdf *.jpg *.jpeg *.png"), ("所有文件", "*.*")],
        )
        self._add_paths(Path(item) for item in selected)

    def _on_drop(self, event) -> None:
        self._add_paths(Path(item) for item in self.root.tk.splitlist(event.data))

    def _add_paths(self, paths) -> None:
        existing = {path.resolve() for path in self.paths}
        rejected = []
        for path in paths:
            path = Path(path)
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                rejected.append(path.name)
                continue
            if path.resolve() in existing:
                continue
            self.paths.append(path)
            existing.add(path.resolve())
            self.file_tree.insert("", tk.END, values=(path.suffix.upper().lstrip("."), str(path)))
        self.status.set(f"已添加 {len(self.paths)} 个文件。")
        if rejected:
            messagebox.showwarning("部分文件未添加", "仅支持PDF、JPG和PNG：\n" + "\n".join(rejected[:10]))

    def _remove_selected(self) -> None:
        indexes = sorted((self.file_tree.index(item) for item in self.file_tree.selection()), reverse=True)
        for index in indexes:
            del self.paths[index]
        self._refresh_file_tree()

    def _clear_files(self) -> None:
        self.paths.clear()
        self._refresh_file_tree()

    def _refresh_file_tree(self) -> None:
        self.file_tree.delete(*self.file_tree.get_children())
        for path in self.paths:
            self.file_tree.insert("", tk.END, values=(path.suffix.upper().lstrip("."), str(path)))
        self.status.set(f"已添加 {len(self.paths)} 个文件。")

    def _select_languages(self, codes) -> None:
        selected = set(codes)
        for code, variable in self.language_vars.items():
            variable.set(code in selected)

    def _selected_language_codes(self) -> tuple[str, ...]:
        return tuple(code for code, variable in self.language_vars.items() if variable.get())

    def _start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if not self.paths:
            messagebox.showinfo("请添加文件", "请先添加PDF、JPG或PNG文件。")
            return
        language_codes = self._selected_language_codes()
        if not language_codes:
            messagebox.showinfo("请选择语言", "请至少选择一种OCR识别语言。")
            return
        settings = OCRSettings(
            language_codes=language_codes,
            dpi=int(self.dpi.get()),
            force_ocr=bool(self.force_ocr.get()),
        )
        paths = list(self.paths)
        include_labels = bool(self.include_labels.get())
        self.cancel_event.clear()
        self.start_button.configure(state=tk.DISABLED)
        self.cancel_button.configure(state=tk.NORMAL)
        self.progress.configure(value=0, maximum=100)
        self.status.set("正在准备提取……")

        def work() -> None:
            try:
                def on_progress(done: int, total: int, message: str) -> None:
                    self.events.put(("progress", done, total, message))

                pages = extract_paths(paths, settings, on_progress, self.cancel_event)
                self.events.put(("done", compose_text(pages, include_labels), pages))
            except OCRCancelled as exc:
                self.events.put(("cancelled", str(exc)))
            except Exception as exc:
                self.events.put(("error", str(exc), traceback.format_exc()))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _cancel(self) -> None:
        self.cancel_event.set()
        self.status.set("正在取消，当前页面完成后停止……")

    def _poll_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "progress":
                    _, done, total, message = event
                    self.progress.configure(maximum=max(1, total), value=done)
                    self.status.set(message)
                elif event[0] == "done":
                    _, text, pages = event
                    self.editor.delete("1.0", tk.END)
                    self.editor.insert("1.0", text)
                    ocr_pages = sum(page.method == "OCR" for page in pages)
                    text_pages = len(pages) - ocr_pages
                    self.status.set(f"提取完成：共{len(pages)}页（OCR {ocr_pages}页，直接提取 {text_pages}页）。")
                    self._finish_worker()
                elif event[0] == "cancelled":
                    self.status.set(event[1])
                    self._finish_worker()
                elif event[0] == "error":
                    self.status.set("提取失败。")
                    self._finish_worker()
                    messagebox.showerror("处理失败", event[1])
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

    def _finish_worker(self) -> None:
        self.start_button.configure(state=tk.NORMAL)
        self.cancel_button.configure(state=tk.DISABLED)

    def _editor_text(self) -> str:
        return self.editor.get("1.0", "end-1c").strip()

    def _export_txt(self) -> None:
        text = self._editor_text()
        if not text:
            messagebox.showinfo("没有内容", "请先提取或输入文字。")
            return
        output = filedialog.asksaveasfilename(
            title="导出TXT",
            defaultextension=".txt",
            filetypes=[("文本文件", "*.txt")],
            initialfile="多语言提取结果.txt",
        )
        if output:
            Path(output).write_text(text, encoding="utf-8-sig")
            self.status.set(f"TXT导出成功：{output}")

    def _export_pdf(self) -> None:
        text = self._editor_text()
        if not text:
            messagebox.showinfo("没有内容", "请先提取或输入文字。")
            return
        output = filedialog.asksaveasfilename(
            title="导出A4 PDF",
            defaultextension=".pdf",
            filetypes=[("PDF文件", "*.pdf")],
            initialfile="多语言提取结果_A4.pdf",
        )
        if not output:
            return
        try:
            export_a4_pdf(
                text,
                Path(output),
                font_size=float(self.font_size.get()),
                margin_mm=float(self.margin_mm.get()),
            )
            self.status.set(f"PDF导出成功：{output}")
            messagebox.showinfo("导出成功", "已按A4连续排版导出PDF。")
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc))


def main() -> None:
    root = TkinterDnD.Tk() if TkinterDnD else tk.Tk()
    OCRApp(root)
    root.mainloop()


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        try:
            from multilang_ocr.self_test import run_self_test

            run_self_test()
        except Exception:
            error_log = Path(tempfile.gettempdir()) / "MultilangOCR-self-test.log"
            error_log.write_text(traceback.format_exc(), encoding="utf-8")
            raise SystemExit(1)
        raise SystemExit(0)
    main()
