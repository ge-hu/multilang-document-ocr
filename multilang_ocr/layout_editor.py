from __future__ import annotations

import queue
import tempfile
import threading
from pathlib import Path
from typing import Callable

import pypdfium2 as pdfium
import tkinter as tk
from PIL import Image, ImageTk
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from tkinter import messagebox, ttk

from .layout import (
    LayoutBlock,
    LayoutSettings,
    WIDTH_AUTO,
    WIDTH_FULL,
    WIDTH_HALF,
    WIDTH_THIRD,
    block_summary,
    blocks_to_text,
    reflow_ocr_text,
    split_text_into_blocks,
)
from .pdf_export import LayoutRenderResult, RenderedBox, export_layout_pdf


MODE_LABEL_TO_VALUE = {
    "智能紧凑": "compact",
    "表格行列": "grid",
    "自由拖动": "free",
}
MODE_VALUE_TO_LABEL = {value: label for label, value in MODE_LABEL_TO_VALUE.items()}


class LayoutEditor(ttk.Frame):
    """A4内容块编辑器；预览和导出共用同一个PDF渲染引擎。"""

    def __init__(self, parent, status_callback: Callable[[str], None]) -> None:
        super().__init__(parent, padding=8)
        self.status_callback = status_callback
        self.blocks: list[LayoutBlock] = []
        self.font_size = tk.DoubleVar(value=5.5)
        self.margin_mm = tk.DoubleVar(value=8.0)
        self.layout_mode = tk.StringVar(value="智能紧凑")
        self.columns = tk.StringVar(value="自动")
        self.column_gap_mm = tk.DoubleVar(value=3.0)
        self.block_gap_mm = tk.DoubleVar(value=1.8)
        self.show_borders = tk.BooleanVar(value=False)
        self.selection_text = tk.StringVar(value="未选择文字框")
        self.page_text = tk.StringVar(value="第 1 / 1 页")
        self.preview_note = tk.StringVar(value="编辑文字或排版参数后自动刷新")

        self._text_guard = False
        self._editing_block_id: str | None = None
        self._preview_page = 0
        self._preview_result: LayoutRenderResult | None = None
        self._preview_source: Image.Image | None = None
        self._preview_photo = None
        self._preview_origin = (0.0, 0.0)
        self._preview_size = (1.0, 1.0)
        self._preview_revision = 0
        self._preview_after_id = None
        self._preview_worker: threading.Thread | None = None
        self._preview_worker_revision = -1
        self._preview_dirty = False
        self._preview_events: queue.Queue[tuple] = queue.Queue()
        self._temp_dir = tempfile.TemporaryDirectory(prefix="multilang_ocr_preview_")

        self._drag_origin: tuple[int, int] | None = None
        self._drag_action: str | None = None
        self._drag_snapshot: dict[str, tuple[int, float, float, float, float]] = {}
        self._drag_source_ids: list[str] = []
        self._drag_moved = False

        self._build_ui()
        self._bind_settings()
        self.after(100, self._poll_preview_events)
        self._schedule_preview()

    def _build_ui(self) -> None:
        controls = ttk.LabelFrame(self, text="A4排版设置", padding=7)
        controls.pack(fill=tk.X)
        ttk.Label(controls, text="字号(pt)").pack(side=tk.LEFT)
        ttk.Spinbox(controls, from_=4.0, to=18.0, increment=0.5, textvariable=self.font_size, width=6).pack(side=tk.LEFT, padx=(3, 10))
        ttk.Label(controls, text="边距(mm)").pack(side=tk.LEFT)
        ttk.Spinbox(controls, from_=5.0, to=25.0, increment=0.5, textvariable=self.margin_mm, width=6).pack(side=tk.LEFT, padx=(3, 10))
        ttk.Label(controls, text="模式").pack(side=tk.LEFT)
        mode_box = ttk.Combobox(
            controls,
            textvariable=self.layout_mode,
            values=tuple(MODE_LABEL_TO_VALUE),
            state="readonly",
            width=9,
        )
        mode_box.pack(side=tk.LEFT, padx=(3, 10))
        mode_box.bind("<<ComboboxSelected>>", self._on_mode_changed)
        ttk.Label(controls, text="列数").pack(side=tk.LEFT)
        ttk.Combobox(controls, textvariable=self.columns, values=("自动", "1", "2", "3"), state="readonly", width=5).pack(side=tk.LEFT, padx=(3, 10))
        ttk.Label(controls, text="栏间距").pack(side=tk.LEFT)
        ttk.Spinbox(controls, from_=0.5, to=12.0, increment=0.5, textvariable=self.column_gap_mm, width=5).pack(side=tk.LEFT, padx=(3, 10))
        ttk.Label(controls, text="块间距").pack(side=tk.LEFT)
        ttk.Spinbox(controls, from_=0.0, to=12.0, increment=0.5, textvariable=self.block_gap_mm, width=5).pack(side=tk.LEFT, padx=(3, 8))
        ttk.Checkbutton(controls, text="导出边框", variable=self.show_borders).pack(side=tk.LEFT)
        ttk.Button(controls, text="一键整齐排列", command=self.arrange_compact).pack(side=tk.RIGHT)

        panes = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        panes.pack(fill=tk.BOTH, expand=True, pady=(7, 0))

        left = ttk.LabelFrame(panes, text="内容块（Ctrl可多选）", padding=6)
        right = ttk.LabelFrame(panes, text="A4实时预览", padding=6)
        panes.add(left, weight=4)
        panes.add(right, weight=7)

        list_frame = ttk.Frame(left)
        list_frame.pack(fill=tk.BOTH, expand=True)
        self.block_tree = ttk.Treeview(
            list_frame,
            columns=("order", "width", "summary"),
            show="headings",
            selectmode="extended",
            height=9,
        )
        self.block_tree.heading("order", text="#")
        self.block_tree.heading("width", text="范围")
        self.block_tree.heading("summary", text="内容")
        self.block_tree.column("order", width=34, anchor=tk.CENTER, stretch=False)
        self.block_tree.column("width", width=56, anchor=tk.CENTER, stretch=False)
        self.block_tree.column("summary", width=270, anchor=tk.W)
        list_scroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.block_tree.yview)
        self.block_tree.configure(yscrollcommand=list_scroll.set)
        list_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.block_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.block_tree.bind("<<TreeviewSelect>>", self._on_tree_selection)

        block_actions = ttk.Frame(left)
        block_actions.pack(fill=tk.X, pady=(5, 5))
        ttk.Button(block_actions, text="新增", command=self.add_block).pack(side=tk.LEFT)
        ttk.Button(block_actions, text="按空行拆分", command=self.split_selected).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(block_actions, text="合并选中", command=self.merge_selected).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(block_actions, text="整理OCR换行", command=self.reflow_selected).pack(side=tk.LEFT, padx=(4, 0))

        ttk.Label(left, textvariable=self.selection_text, foreground="#1D4ED8").pack(fill=tk.X)
        editor_frame = ttk.Frame(left)
        editor_frame.pack(fill=tk.BOTH, expand=True, pady=(3, 0))
        self.text_editor = tk.Text(editor_frame, wrap=tk.WORD, undo=True, font=("Microsoft YaHei UI", 9))
        editor_scroll = ttk.Scrollbar(editor_frame, orient=tk.VERTICAL, command=self.text_editor.yview)
        self.text_editor.configure(yscrollcommand=editor_scroll.set)
        editor_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.text_editor.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.text_editor.bind("<<Modified>>", self._on_text_modified)
        self.text_editor.configure(state=tk.DISABLED)

        preview_nav = ttk.Frame(right)
        preview_nav.pack(fill=tk.X)
        ttk.Button(preview_nav, text="◀ 上一页", command=lambda: self.change_page(-1)).pack(side=tk.LEFT)
        ttk.Label(preview_nav, textvariable=self.page_text).pack(side=tk.LEFT, padx=8)
        ttk.Button(preview_nav, text="下一页 ▶", command=lambda: self.change_page(1)).pack(side=tk.LEFT)
        ttk.Label(preview_nav, textvariable=self.preview_note, foreground="#166534").pack(side=tk.RIGHT)

        self.preview_host = ttk.Frame(right)
        self.preview_host.pack(fill=tk.BOTH, expand=True, pady=(5, 0))
        self.preview_canvas = tk.Canvas(self.preview_host, background="#D6D9DE", highlightthickness=0, cursor="arrow")
        self.preview_canvas.pack(fill=tk.BOTH, expand=True)
        self.preview_canvas.bind("<Configure>", lambda _event: self._paint_preview())
        self.preview_canvas.bind("<ButtonPress-1>", self._on_canvas_press)
        self.preview_canvas.bind("<B1-Motion>", self._on_canvas_motion)
        self.preview_canvas.bind("<ButtonRelease-1>", self._on_canvas_release)

        self.float_bar = tk.Frame(self.preview_host, background="#1F2937", padx=5, pady=4, bd=0)
        self.float_label = tk.Label(self.float_bar, text="", foreground="white", background="#1F2937")
        self.float_label.pack(side=tk.LEFT, padx=(2, 5))
        for label, units in (("自动", WIDTH_AUTO), ("整行", WIDTH_FULL), ("1/2", WIDTH_HALF), ("1/3", WIDTH_THIRD)):
            tk.Button(
                self.float_bar,
                text=label,
                command=lambda value=units: self.set_selected_width(value),
                relief=tk.FLAT,
                padx=5,
                pady=1,
            ).pack(side=tk.LEFT, padx=1)
        tk.Button(self.float_bar, text="上移", command=lambda: self.move_selected(-1), relief=tk.FLAT, padx=5, pady=1).pack(side=tk.LEFT, padx=1)
        tk.Button(self.float_bar, text="下移", command=lambda: self.move_selected(1), relief=tk.FLAT, padx=5, pady=1).pack(side=tk.LEFT, padx=1)
        tk.Button(self.float_bar, text="适应高度", command=self.fit_selected_height, relief=tk.FLAT, padx=5, pady=1).pack(side=tk.LEFT, padx=1)
        tk.Button(self.float_bar, text="删除", command=self.delete_selected, relief=tk.FLAT, padx=5, pady=1).pack(side=tk.LEFT, padx=1)

    def _bind_settings(self) -> None:
        for variable in (
            self.font_size,
            self.margin_mm,
            self.columns,
            self.column_gap_mm,
            self.block_gap_mm,
            self.show_borders,
        ):
            variable.trace_add("write", self._on_setting_changed)

    def _on_setting_changed(self, *_args) -> None:
        self._schedule_preview()

    def settings(self) -> LayoutSettings:
        def number(variable, fallback: float) -> float:
            try:
                return float(variable.get())
            except (tk.TclError, ValueError):
                return fallback

        try:
            columns = 0 if self.columns.get() == "自动" else int(self.columns.get())
        except (tk.TclError, ValueError):
            columns = 2
        return LayoutSettings(
            font_size=number(self.font_size, 5.5),
            margin_mm=number(self.margin_mm, 8.0),
            layout_mode=MODE_LABEL_TO_VALUE.get(self.layout_mode.get(), "compact"),
            columns=columns,
            column_gap_mm=number(self.column_gap_mm, 3.0),
            block_gap_mm=number(self.block_gap_mm, 1.8),
            show_borders=bool(self.show_borders.get()),
        ).normalized()

    def load_text(self, text: str, *, reflow: bool = True) -> None:
        self.blocks = split_text_into_blocks(text, reflow=reflow, default_width_units=WIDTH_AUTO)
        self._preview_page = 0
        self._refresh_tree()
        if self.blocks:
            self.block_tree.selection_set(self.blocks[0].block_id)
            self.block_tree.focus(self.blocks[0].block_id)
            self.block_tree.see(self.blocks[0].block_id)
        self._on_tree_selection()
        self._schedule_preview()
        self.status_callback(f"已生成 {len(self.blocks)} 个可编辑内容块。")

    def get_text(self) -> str:
        self._commit_editor()
        return blocks_to_text(self.blocks)

    def export_pdf(self, output_path: Path) -> LayoutRenderResult:
        self._commit_editor()
        return export_layout_pdf(self.blocks, output_path, self.settings(), strict=True)

    def _refresh_tree(self, selected: list[str] | None = None) -> None:
        if selected is None:
            selected = list(self.block_tree.selection()) if hasattr(self, "block_tree") else []
        self.block_tree.delete(*self.block_tree.get_children())
        for index, block in enumerate(self.blocks, 1):
            self.block_tree.insert(
                "",
                tk.END,
                iid=block.block_id,
                values=(index, self._width_label(block.width_units), block_summary(block)),
            )
        existing = [item for item in selected if self.block_tree.exists(item)]
        if existing:
            self.block_tree.selection_set(existing)

    @staticmethod
    def _width_label(units: int) -> str:
        return {WIDTH_AUTO: "自动", WIDTH_FULL: "整行", WIDTH_HALF: "1/2", WIDTH_THIRD: "1/3"}.get(units, "自定")

    def _block_by_id(self, block_id: str) -> LayoutBlock | None:
        return next((block for block in self.blocks if block.block_id == block_id), None)

    def _selected_ids(self) -> list[str]:
        selected = set(self.block_tree.selection())
        return [block.block_id for block in self.blocks if block.block_id in selected]

    def _on_tree_selection(self, _event=None) -> None:
        self._commit_editor()
        selected = self._selected_ids()
        self._text_guard = True
        self.text_editor.configure(state=tk.NORMAL)
        self.text_editor.delete("1.0", tk.END)
        if len(selected) == 1:
            block = self._block_by_id(selected[0])
            if block:
                self.text_editor.insert("1.0", block.text)
            self._editing_block_id = selected[0]
            self.selection_text.set("正在编辑 1 个文字框")
        elif len(selected) > 1:
            self.text_editor.insert("1.0", "已多选文字框，请使用上方悬浮工具栏批量调整。")
            self.text_editor.configure(state=tk.DISABLED)
            self._editing_block_id = None
            self.selection_text.set(f"已选择 {len(selected)} 个文字框，可批量修改")
        else:
            self.text_editor.configure(state=tk.DISABLED)
            self._editing_block_id = None
            self.selection_text.set("未选择文字框")
        self.text_editor.edit_modified(False)
        self._text_guard = False
        self._show_float_bar()
        self._paint_preview()

    def _commit_editor(self) -> None:
        if self._text_guard or not hasattr(self, "text_editor"):
            return
        if not self._editing_block_id or str(self.text_editor.cget("state")) == tk.DISABLED:
            return
        block = self._block_by_id(self._editing_block_id)
        if not block:
            return
        value = self.text_editor.get("1.0", "end-1c")
        if value == block.text:
            return
        block.text = value
        if self.block_tree.exists(block.block_id):
            values = list(self.block_tree.item(block.block_id, "values"))
            values[2] = block_summary(block)
            self.block_tree.item(block.block_id, values=values)

    def _on_text_modified(self, _event=None) -> None:
        if self._text_guard or not self.text_editor.edit_modified():
            return
        self.text_editor.edit_modified(False)
        self._commit_editor()
        self._schedule_preview()

    def add_block(self) -> None:
        selected = self._selected_ids()
        insert_at = len(self.blocks)
        if selected:
            insert_at = max(index for index, block in enumerate(self.blocks) if block.block_id in selected) + 1
        block = LayoutBlock.create("请输入文字", WIDTH_AUTO)
        self.blocks.insert(insert_at, block)
        self._refresh_tree([block.block_id])
        self._on_tree_selection()
        self.text_editor.focus_set()
        self.text_editor.tag_add(tk.SEL, "1.0", tk.END)
        self._schedule_preview()

    def split_selected(self) -> None:
        self._commit_editor()
        selected = self._selected_ids()
        if len(selected) != 1:
            messagebox.showinfo("请选择一个文字框", "请先选择一个包含空行的文字框。")
            return
        index = next(i for i, block in enumerate(self.blocks) if block.block_id == selected[0])
        source = self.blocks[index]
        parts = split_text_into_blocks(source.text, default_width_units=source.width_units)
        if len(parts) < 2:
            messagebox.showinfo("无法拆分", "该文字框中没有可用于拆分的空行。")
            return
        parts[0].block_id = source.block_id
        self.blocks[index : index + 1] = parts
        self._refresh_tree([part.block_id for part in parts])
        self._on_tree_selection()
        self._schedule_preview()

    def merge_selected(self) -> None:
        self._commit_editor()
        selected = set(self._selected_ids())
        if len(selected) < 2:
            messagebox.showinfo("请选择多个文字框", "按住Ctrl选择两个或更多文字框后再合并。")
            return
        indexes = [index for index, block in enumerate(self.blocks) if block.block_id in selected]
        first = self.blocks[indexes[0]]
        merged = "\n\n".join(self.blocks[index].text.strip() for index in indexes)
        first.text = merged
        self.blocks = [block for index, block in enumerate(self.blocks) if index not in indexes[1:]]
        self._refresh_tree([first.block_id])
        self._on_tree_selection()
        self._schedule_preview()

    def reflow_selected(self) -> None:
        self._commit_editor()
        selected = set(self._selected_ids())
        targets = [block for block in self.blocks if not selected or block.block_id in selected]
        if not targets:
            return
        for block in targets:
            block.text = reflow_ocr_text(block.text)
        self._refresh_tree([block.block_id for block in targets])
        self._on_tree_selection()
        self._schedule_preview()
        self.status_callback(f"已整理 {len(targets)} 个文字框中的OCR换行。")

    def delete_selected(self) -> None:
        selected = set(self._selected_ids())
        if not selected:
            return
        self.blocks = [block for block in self.blocks if block.block_id not in selected]
        self._refresh_tree()
        if self.blocks:
            self.block_tree.selection_set(self.blocks[0].block_id)
        self._on_tree_selection()
        self._schedule_preview()

    def set_selected_width(self, width_units: int) -> None:
        selected = set(self._selected_ids())
        if not selected:
            return
        settings = self.settings()
        printable_width = 210.0 - 2 * settings.margin_mm
        for block in self.blocks:
            if block.block_id not in selected:
                continue
            block.width_units = width_units
            if settings.layout_mode == "free" and block.free_x_mm is not None:
                if width_units == WIDTH_AUTO:
                    divisor = 3 if len(self.blocks) >= 9 else 2
                    block.free_w_mm = printable_width / divisor
                else:
                    block.free_w_mm = printable_width * width_units / 6.0
                block.free_x_mm = min(block.free_x_mm, 210.0 - settings.margin_mm - block.free_w_mm)
        self._refresh_tree(list(selected))
        self._show_float_bar()
        self._schedule_preview()

    def move_selected(self, direction: int) -> None:
        selected = set(self._selected_ids())
        if not selected:
            return
        if direction < 0:
            for index in range(1, len(self.blocks)):
                if self.blocks[index].block_id in selected and self.blocks[index - 1].block_id not in selected:
                    self.blocks[index - 1], self.blocks[index] = self.blocks[index], self.blocks[index - 1]
        else:
            for index in range(len(self.blocks) - 2, -1, -1):
                if self.blocks[index].block_id in selected and self.blocks[index + 1].block_id not in selected:
                    self.blocks[index], self.blocks[index + 1] = self.blocks[index + 1], self.blocks[index]
        self._refresh_tree(list(selected))
        self._schedule_preview()

    def fit_selected_height(self) -> None:
        if self.settings().layout_mode != "free" or not self._preview_result:
            self.status_callback("“适应高度”仅用于自由拖动模式。")
            return
        selected = set(self._selected_ids())
        if not selected:
            return
        boxes = {box.block_id: box for box in self._preview_result.boxes if box.fragment_index == 0}
        for block in self.blocks:
            box = boxes.get(block.block_id)
            if block.block_id in selected and box:
                block.free_h_mm = box.height_pt / mm
        self._schedule_preview()

    def arrange_compact(self) -> None:
        self.layout_mode.set("智能紧凑")
        self._schedule_preview()
        self.status_callback("已切换为智能紧凑排列；内容会自动补充页面空位。")

    def _on_mode_changed(self, _event=None) -> None:
        if MODE_LABEL_TO_VALUE.get(self.layout_mode.get()) == "free":
            self._seed_free_positions()
            self.status_callback("自由拖动已开启：拖动文字框移动，拖右下角可缩放。")
        self._schedule_preview()
        self._paint_preview()

    def _seed_free_positions(self) -> None:
        if not self.blocks or all(block.free_x_mm is not None for block in self.blocks):
            return
        grouped: dict[str, list[RenderedBox]] = {}
        if self._preview_result:
            for box in self._preview_result.boxes:
                grouped.setdefault(box.block_id, []).append(box)
        margin = self.settings().margin_mm
        fallback_y = margin
        for block in self.blocks:
            boxes = grouped.get(block.block_id, [])
            if boxes:
                first = boxes[0]
                block.free_page = first.page_index
                block.free_x_mm = first.x_pt / mm
                block.free_y_mm = first.y_pt / mm
                block.free_w_mm = first.width_pt / mm
                block.free_h_mm = first.height_pt / mm
            else:
                block.free_page = 0
                block.free_x_mm = margin
                block.free_y_mm = fallback_y
                block.free_w_mm = (210.0 - 2 * margin) / 2.0
                block.free_h_mm = 12.0
                fallback_y += 14.0

    def _show_float_bar(self) -> None:
        count = len(self._selected_ids())
        if count:
            self.float_label.configure(text=f"已选{count}项")
            self.float_bar.place(relx=0.5, y=38, anchor=tk.N)
            self.float_bar.lift()
        else:
            self.float_bar.place_forget()

    def change_page(self, delta: int) -> None:
        page_count = self._preview_result.page_count if self._preview_result else 1
        page = min(page_count - 1, max(0, self._preview_page + delta))
        if page == self._preview_page:
            return
        self._preview_page = page
        self._schedule_preview(delay=30)

    def _schedule_preview(self, *_args, delay: int = 350) -> None:
        self._preview_revision += 1
        self._preview_dirty = True
        if self._preview_after_id is not None:
            try:
                self.after_cancel(self._preview_after_id)
            except tk.TclError:
                pass
        self._preview_after_id = self.after(delay, self._launch_preview)

    def _launch_preview(self) -> None:
        self._preview_after_id = None
        if self._preview_worker and self._preview_worker.is_alive():
            self._preview_dirty = True
            return
        revision = self._preview_revision
        blocks = [block.clone() for block in self.blocks]
        settings = self.settings()
        requested_page = self._preview_page
        output_path = Path(self._temp_dir.name) / f"preview-{revision}.pdf"
        self._preview_dirty = False
        self.preview_note.set("正在刷新预览…")

        def work() -> None:
            try:
                result = export_layout_pdf(blocks, output_path, settings, strict=False)
                page_index = min(max(0, requested_page), result.page_count - 1)
                image = self._render_pdf_page(output_path, page_index)
                self._preview_events.put(("preview", revision, result, page_index, image))
            except Exception as exc:
                self._preview_events.put(("preview_error", revision, str(exc)))

        self._preview_worker_revision = revision
        self._preview_worker = threading.Thread(target=work, daemon=True)
        self._preview_worker.start()

    @staticmethod
    def _render_pdf_page(path: Path, page_index: int) -> Image.Image:
        document = pdfium.PdfDocument(str(path))
        page = None
        bitmap = None
        try:
            page = document[page_index]
            bitmap = page.render(scale=1.55)
            return bitmap.to_pil().convert("RGB").copy()
        finally:
            if bitmap is not None:
                bitmap.close()
            if page is not None:
                page.close()
            document.close()

    def _poll_preview_events(self) -> None:
        try:
            while True:
                event = self._preview_events.get_nowait()
                revision = event[1]
                if revision == self._preview_worker_revision:
                    self._preview_worker = None
                if revision != self._preview_revision:
                    if self._preview_dirty and self._preview_worker is None:
                        self._schedule_preview(delay=40)
                    continue
                if event[0] == "preview":
                    _, _, result, page_index, image = event
                    self._preview_result = result
                    self._preview_page = page_index
                    self._preview_source = image
                    self.page_text.set(f"第 {page_index + 1} / {result.page_count} 页")
                    if result.warnings:
                        self.preview_note.set("；".join(result.warnings[:2]))
                    else:
                        self.preview_note.set("预览与导出使用同一排版结果")
                    self._paint_preview()
                else:
                    self.preview_note.set("预览失败：" + event[2])
                if self._preview_dirty or revision != self._preview_revision:
                    self._schedule_preview(delay=40)
        except queue.Empty:
            pass
        self.after(100, self._poll_preview_events)

    def _paint_preview(self) -> None:
        if not hasattr(self, "preview_canvas"):
            return
        canvas_widget = self.preview_canvas
        canvas_widget.delete("all")
        width = max(100, canvas_widget.winfo_width())
        height = max(100, canvas_widget.winfo_height())
        padding = 14
        page_ratio = A4[0] / A4[1]
        available_w = max(40, width - 2 * padding)
        available_h = max(40, height - 2 * padding)
        display_w = min(available_w, available_h * page_ratio)
        display_h = display_w / page_ratio
        origin_x = (width - display_w) / 2
        origin_y = (height - display_h) / 2
        self._preview_origin = (origin_x, origin_y)
        self._preview_size = (display_w, display_h)
        canvas_widget.create_rectangle(
            origin_x + 3,
            origin_y + 4,
            origin_x + display_w + 3,
            origin_y + display_h + 4,
            fill="#AEB3BA",
            outline="",
        )
        if self._preview_source:
            resized = self._preview_source.resize(
                (max(1, int(display_w)), max(1, int(display_h))),
                Image.Resampling.LANCZOS,
            )
            self._preview_photo = ImageTk.PhotoImage(resized)
            canvas_widget.create_image(origin_x, origin_y, anchor=tk.NW, image=self._preview_photo)
        else:
            canvas_widget.create_rectangle(origin_x, origin_y, origin_x + display_w, origin_y + display_h, fill="white", outline="#9CA3AF")
            canvas_widget.create_text(width / 2, height / 2, text="等待A4预览", fill="#6B7280")

        selected = set(self._selected_ids()) if hasattr(self, "block_tree") else set()
        boxes = self._display_boxes()
        for box in boxes:
            if box.page_index != self._preview_page:
                continue
            x1, y1, x2, y2 = self._box_to_canvas(box)
            active = box.block_id in selected
            canvas_widget.create_rectangle(
                x1,
                y1,
                x2,
                y2,
                outline="#2563EB" if active else "#94A3B8",
                width=2 if active else 1,
                dash=() if active else (3, 3),
            )
            if active and len(selected) == 1:
                canvas_widget.create_rectangle(x2 - 5, y2 - 5, x2 + 5, y2 + 5, fill="#2563EB", outline="white")
        self._show_float_bar()

    def _display_boxes(self) -> list[RenderedBox]:
        if self.settings().layout_mode == "free":
            boxes = []
            for block in self.blocks:
                if None in (block.free_x_mm, block.free_y_mm, block.free_w_mm, block.free_h_mm):
                    continue
                boxes.append(
                    RenderedBox(
                        block.block_id,
                        block.free_page,
                        block.free_x_mm * mm,
                        block.free_y_mm * mm,
                        block.free_w_mm * mm,
                        block.free_h_mm * mm,
                    )
                )
            return boxes
        return list(self._preview_result.boxes) if self._preview_result else []

    def _box_to_canvas(self, box: RenderedBox) -> tuple[float, float, float, float]:
        origin_x, origin_y = self._preview_origin
        display_w, display_h = self._preview_size
        x1 = origin_x + box.x_pt / A4[0] * display_w
        y1 = origin_y + box.y_pt / A4[1] * display_h
        x2 = x1 + box.width_pt / A4[0] * display_w
        y2 = y1 + box.height_pt / A4[1] * display_h
        return x1, y1, x2, y2

    def _hit_test(self, x: float, y: float) -> tuple[RenderedBox | None, bool]:
        selected = set(self._selected_ids())
        boxes = [box for box in self._display_boxes() if box.page_index == self._preview_page]
        for box in reversed(boxes):
            x1, y1, x2, y2 = self._box_to_canvas(box)
            if box.block_id in selected and len(selected) == 1 and abs(x - x2) <= 9 and abs(y - y2) <= 9:
                return box, True
            if x1 <= x <= x2 and y1 <= y <= y2:
                return box, False
        return None, False

    def _on_canvas_press(self, event) -> None:
        box, resize = self._hit_test(event.x, event.y)
        ctrl = bool(event.state & 0x0004)
        if not box:
            if not ctrl:
                self.block_tree.selection_remove(*self.block_tree.selection())
                self._on_tree_selection()
            return
        current = set(self._selected_ids())
        if ctrl:
            if box.block_id in current:
                self.block_tree.selection_remove(box.block_id)
            else:
                self.block_tree.selection_add(box.block_id)
        elif box.block_id not in current:
            self.block_tree.selection_set(box.block_id)
        self.block_tree.focus(box.block_id)
        self.block_tree.see(box.block_id)
        self._on_tree_selection()
        self._drag_origin = (event.x, event.y)
        self._drag_moved = False
        self._drag_source_ids = self._selected_ids()
        if self.settings().layout_mode == "free":
            self._seed_free_positions()
            self._drag_action = "resize" if resize and len(self._drag_source_ids) == 1 else "move"
            self._drag_snapshot = {
                block.block_id: (
                    block.free_page,
                    float(block.free_x_mm),
                    float(block.free_y_mm),
                    float(block.free_w_mm),
                    float(block.free_h_mm),
                )
                for block in self.blocks
                if block.block_id in self._drag_source_ids and block.free_x_mm is not None
            }
        else:
            self._drag_action = "reorder"

    def _on_canvas_motion(self, event) -> None:
        if not self._drag_origin or not self._drag_action:
            return
        dx_pixels = event.x - self._drag_origin[0]
        dy_pixels = event.y - self._drag_origin[1]
        if abs(dx_pixels) + abs(dy_pixels) > 4:
            self._drag_moved = True
        if self._drag_action not in {"move", "resize"}:
            return
        display_w, display_h = self._preview_size
        dx_mm = dx_pixels / max(1.0, display_w) * 210.0
        dy_mm = dy_pixels / max(1.0, display_h) * 297.0
        settings = self.settings()
        margin = settings.margin_mm
        for block in self.blocks:
            snapshot = self._drag_snapshot.get(block.block_id)
            if not snapshot:
                continue
            page, x, y, width, height = snapshot
            if self._drag_action == "move":
                block.free_x_mm = self._snap(min(210.0 - margin - width, max(margin, x + dx_mm)))
                block.free_y_mm = self._snap(min(297.0 - margin - height, max(margin, y + dy_mm)))
            else:
                block.free_w_mm = self._snap(min(210.0 - margin - x, max(15.0, width + dx_mm)))
                block.free_h_mm = self._snap(min(297.0 - margin - y, max(8.0, height + dy_mm)))
            block.free_page = page
        self._paint_preview()
        self._schedule_preview(delay=120)

    def _on_canvas_release(self, event) -> None:
        if not self._drag_origin or not self._drag_action:
            return
        action = self._drag_action
        if action in {"move", "resize"}:
            if self._free_layout_overlaps():
                for block in self.blocks:
                    snapshot = self._drag_snapshot.get(block.block_id)
                    if snapshot:
                        block.free_page, block.free_x_mm, block.free_y_mm, block.free_w_mm, block.free_h_mm = snapshot
                self.status_callback("文字框不能重叠，已恢复拖动前的位置。")
            self._schedule_preview(delay=40)
        elif action == "reorder" and self._drag_moved:
            target, _ = self._hit_test(event.x, event.y)
            if target and target.block_id not in self._drag_source_ids:
                self._reorder_before(self._drag_source_ids, target.block_id)
        self._drag_origin = None
        self._drag_action = None
        self._drag_snapshot = {}
        self._drag_source_ids = []
        self._drag_moved = False

    @staticmethod
    def _snap(value: float) -> float:
        return round(value * 2.0) / 2.0

    def _free_layout_overlaps(self) -> bool:
        rects = []
        for block in self.blocks:
            if None in (block.free_x_mm, block.free_y_mm, block.free_w_mm, block.free_h_mm):
                continue
            rects.append((block.free_page, block.free_x_mm, block.free_y_mm, block.free_w_mm, block.free_h_mm))
        for index, left in enumerate(rects):
            for right in rects[index + 1 :]:
                if left[0] != right[0]:
                    continue
                if not (
                    left[1] + left[3] <= right[1]
                    or right[1] + right[3] <= left[1]
                    or left[2] + left[4] <= right[2]
                    or right[2] + right[4] <= left[2]
                ):
                    return True
        return False

    def _reorder_before(self, source_ids: list[str], target_id: str) -> None:
        source = set(source_ids)
        moving = [block for block in self.blocks if block.block_id in source]
        remaining = [block for block in self.blocks if block.block_id not in source]
        target_index = next((index for index, block in enumerate(remaining) if block.block_id == target_id), len(remaining))
        self.blocks = remaining[:target_index] + moving + remaining[target_index:]
        self._refresh_tree(source_ids)
        self._schedule_preview(delay=40)

    def destroy(self) -> None:
        try:
            self._temp_dir.cleanup()
        except Exception:
            pass
        super().destroy()
