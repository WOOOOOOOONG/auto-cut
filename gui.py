#!/usr/bin/env python3
"""Tkinter GUI for auto-cut.

Pick a video, tweak settings, hit Run. The pipeline runs in a worker
thread and streams log lines back to the UI via a queue.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from auto_cut import Config, run_pipeline


SETTINGS = [
    ("target_minutes",   "Target length (min)",  20.0,  "최종 영상 목표 길이"),
    ("rms_percentile",   "RMS percentile",       70.0,  "교전 임계값 (낮출수록 더 많이 잡힘)"),
    ("min_loud",         "Min loud (sec)",        3.0,  "이 시간 이상 시끄러워야 교전으로 인정"),
    ("merge_gap",        "Merge gap (sec)",       3.0,  "이 간격 이내 클립끼리 병합"),
    ("min_clip",         "Min clip (sec)",        5.0,  "이보다 짧으면 버림"),
    ("pad_before",       "Pad before (sec)",      2.0,  "클립 앞 여유"),
    ("pad_after",        "Pad after (sec)",       3.0,  "클립 뒤 여유"),
    ("scene_threshold",  "Scene threshold",      27.0,  "장면 전환 민감도 (낮을수록 많이 잡힘)"),
]

VIDEO_TYPES = [
    ("Video", "*.mp4 *.mkv *.mov *.avi *.webm"),
    ("All files", "*.*"),
]


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("auto-cut")
        self.geometry("720x640")
        self.minsize(640, 560)

        self.video_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.setting_vars: dict[str, tk.DoubleVar] = {}
        self.log_queue: queue.Queue[str | None] = queue.Queue()
        self.worker: threading.Thread | None = None

        self._build()
        self.after(100, self._poll_log)

    def _build(self) -> None:
        pad = {"padx": 8, "pady": 4}

        # Input video
        row = ttk.Frame(self)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="Video", width=12).pack(side="left")
        ttk.Entry(row, textvariable=self.video_var).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(row, text="Browse...", command=self._pick_video).pack(side="left")

        # Output EDL
        row = ttk.Frame(self)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="Output EDL", width=12).pack(side="left")
        ttk.Entry(row, textvariable=self.output_var).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(row, text="Browse...", command=self._pick_output).pack(side="left")

        # Settings frame (2 columns)
        settings = ttk.LabelFrame(self, text="Settings")
        settings.pack(fill="x", **pad)
        for i, (key, label, default, tip) in enumerate(SETTINGS):
            r, c = divmod(i, 2)
            cell = ttk.Frame(settings)
            cell.grid(row=r, column=c, sticky="ew", padx=8, pady=4)
            settings.grid_columnconfigure(c, weight=1)
            ttk.Label(cell, text=label, width=20).pack(side="left")
            var = tk.DoubleVar(value=default)
            self.setting_vars[key] = var
            entry = ttk.Entry(cell, textvariable=var, width=10)
            entry.pack(side="left")
            self._tooltip(entry, tip)

        # Buttons
        row = ttk.Frame(self)
        row.pack(fill="x", **pad)
        self.run_btn = ttk.Button(row, text="Run", command=self._run)
        self.run_btn.pack(side="left", padx=4)
        ttk.Button(row, text="Reset settings", command=self._reset).pack(side="left", padx=4)
        ttk.Button(row, text="Open output folder", command=self._open_output_dir).pack(side="left", padx=4)

        # Log
        log_frame = ttk.LabelFrame(self, text="Log")
        log_frame.pack(fill="both", expand=True, **pad)
        self.log = tk.Text(log_frame, wrap="none", height=12, state="disabled",
                           font=("Consolas", 10))
        self.log.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(log_frame, command=self.log.yview)
        sb.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=sb.set)

        # Status bar
        self.status = tk.StringVar(value="Ready.")
        ttk.Label(self, textvariable=self.status, anchor="w",
                  relief="sunken").pack(fill="x", side="bottom")

    def _tooltip(self, widget: tk.Widget, text: str) -> None:
        tip: tk.Toplevel | None = None

        def show(_event=None):
            nonlocal tip
            if tip is not None:
                return
            x = widget.winfo_rootx() + 20
            y = widget.winfo_rooty() + widget.winfo_height() + 4
            tip = tk.Toplevel(widget)
            tip.wm_overrideredirect(True)
            tip.wm_geometry(f"+{x}+{y}")
            ttk.Label(tip, text=text, background="#ffffe0",
                      relief="solid", borderwidth=1, padding=4).pack()

        def hide(_event=None):
            nonlocal tip
            if tip is not None:
                tip.destroy()
                tip = None

        widget.bind("<Enter>", show)
        widget.bind("<Leave>", hide)

    def _pick_video(self) -> None:
        path = filedialog.askopenfilename(title="Select video", filetypes=VIDEO_TYPES)
        if not path:
            return
        self.video_var.set(path)
        if not self.output_var.get():
            self.output_var.set(str(Path(path).with_suffix(".edl")))

    def _pick_output(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Save EDL as", defaultextension=".edl",
            filetypes=[("EDL", "*.edl"), ("All files", "*.*")],
        )
        if path:
            self.output_var.set(path)

    def _reset(self) -> None:
        for key, _label, default, _tip in SETTINGS:
            self.setting_vars[key].set(default)

    def _open_output_dir(self) -> None:
        path = self.output_var.get()
        if not path:
            return
        d = Path(path).parent
        if not d.exists():
            return
        import subprocess, sys
        if sys.platform == "win32":
            subprocess.Popen(["explorer", str(d)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(d)])
        else:
            subprocess.Popen(["xdg-open", str(d)])

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _poll_log(self) -> None:
        try:
            while True:
                msg = self.log_queue.get_nowait()
                if msg is None:
                    self._on_done()
                else:
                    self._append_log(msg)
        except queue.Empty:
            pass
        self.after(100, self._poll_log)

    def _on_done(self) -> None:
        self.run_btn.configure(state="normal")
        self.status.set("Done.")

    def _run(self) -> None:
        video = self.video_var.get().strip()
        output = self.output_var.get().strip()
        if not video:
            messagebox.showerror("auto-cut", "Pick a video first.")
            return
        if not Path(video).exists():
            messagebox.showerror("auto-cut", f"Video not found:\n{video}")
            return
        if not output:
            output = str(Path(video).with_suffix(".edl"))
            self.output_var.set(output)

        try:
            config = Config(**{k: float(v.get()) for k, v in self.setting_vars.items()})
        except (tk.TclError, ValueError) as e:
            messagebox.showerror("auto-cut", f"Invalid setting value:\n{e}")
            return

        self.run_btn.configure(state="disabled")
        self.status.set("Running...")
        self._append_log(f"\n=== Run: {Path(video).name} ===")

        def log(msg: str) -> None:
            self.log_queue.put(msg)

        def worker() -> None:
            try:
                run_pipeline(Path(video), Path(output), config, log=log)
            except Exception as e:  # noqa: BLE001
                log(f"ERROR: {e}")
            finally:
                self.log_queue.put(None)

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()
