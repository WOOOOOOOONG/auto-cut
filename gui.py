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
    ("target_minutes",   "Target length (min)",  20.0,
     "최종 영상 길이 (분)\n"
     "클립 합계가 이 값을 넘으면 에너지(소리) 높은 순으로 잘려나감.\n"
     "예: 20 = 최종 20분짜리 영상 목표."),
    ("rms_percentile",   "RMS percentile",       70.0,
     "교전 임계값 (퍼센타일, 0~100)\n"
     "오디오 RMS의 이 분위수보다 큰 구간을 '교전'으로 판단.\n"
     "낮출수록 더 많은 구간 후보 (60: 잔잔한 부분도 포함).\n"
     "높일수록 큰 굉음만 (80: 폭발·강력한 총격만)."),
    ("min_loud",         "Min loud (sec)",        3.0,
     "교전 최소 지속 시간 (초)\n"
     "시끄러운 구간이 이 시간 이상 지속되어야 클립 후보로 인정.\n"
     "낮추면 짧은 단발성 소리(수류탄 한 발 등)도 잡힘."),
    ("merge_gap",        "Merge gap (sec)",       3.0,
     "클립 병합 간격 (초)\n"
     "두 클립 사이 간격이 이 값보다 작으면 하나로 합침.\n"
     "자잘한 컷이 너무 많으면 키움. 컷이 너무 길게 합쳐지면 줄임."),
    ("min_clip",         "Min clip (sec)",        5.0,
     "최소 클립 길이 (초)\n"
     "이보다 짧은 클립은 결과에서 제외.\n"
     "자잘한 컷 정리에 효과적."),
    ("pad_before",       "Pad before (sec)",      2.0,
     "앞 여유 (초)\n"
     "각 클립 시작 전에 추가하는 여유분.\n"
     "교전 직전 분위기·진입 구간을 살리고 싶으면 키움."),
    ("pad_after",        "Pad after (sec)",       3.0,
     "뒤 여유 (초)\n"
     "각 클립 끝에 추가하는 여유분.\n"
     "교전 마무리·정리 구간을 보여주고 싶으면 키움."),
    ("scene_threshold",  "Scene threshold",      27.0,
     "장면 전환 민감도\n"
     "낮을수록 화면 변화에 민감해 더 많은 장면 경계를 잡음 (예: 20).\n"
     "높일수록 큰 변화만 장면으로 인정 (예: 35).\n"
     "라운드 리셋·메뉴 화면이 잘 안 잡히면 낮추기."),
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
            lbl = ttk.Label(cell, text=label, width=20, cursor="question_arrow")
            lbl.pack(side="left")
            var = tk.DoubleVar(value=default)
            self.setting_vars[key] = var
            entry = ttk.Entry(cell, textvariable=var, width=10)
            entry.pack(side="left")
            for w in (lbl, entry):
                self._tooltip(w, tip)

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

    def _tooltip(self, widget: tk.Widget, text: str, delay_ms: int = 400) -> None:
        tip: tk.Toplevel | None = None
        scheduled: str | None = None

        def show():
            nonlocal tip
            if tip is not None:
                return
            x = widget.winfo_rootx() + 20
            y = widget.winfo_rooty() + widget.winfo_height() + 4
            tip = tk.Toplevel(widget)
            tip.wm_overrideredirect(True)
            tip.wm_attributes("-topmost", True)
            tip.wm_geometry(f"+{x}+{y}")
            ttk.Label(
                tip, text=text, background="#ffffe0",
                foreground="#222", relief="solid", borderwidth=1,
                padding=(8, 6), justify="left", wraplength=420,
            ).pack()

        def schedule(_event=None):
            nonlocal scheduled
            cancel()
            scheduled = widget.after(delay_ms, show)

        def cancel(_event=None):
            nonlocal scheduled, tip
            if scheduled is not None:
                widget.after_cancel(scheduled)
                scheduled = None
            if tip is not None:
                tip.destroy()
                tip = None

        widget.bind("<Enter>", schedule)
        widget.bind("<Leave>", cancel)
        widget.bind("<ButtonPress>", cancel)

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
