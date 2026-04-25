#!/usr/bin/env python3
"""Tkinter GUI for auto-cut.

Two tabs:
  1. 컷편집 (Cut)    — audio + scene-based EDL generation.
  2. 대본 생성 (Script) — Claude Code-driven voiceover script generation
                       from the EDL + keyframes + the user's past scripts.

The log area at the bottom is shared so both pipelines stream into the
same widget.
"""

from __future__ import annotations

import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from auto_cut import Config, run_pipeline
import auto_script


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
        self.geometry("780x780")
        self.minsize(720, 700)

        # Cut tab state
        self.video_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.setting_vars: dict[str, tk.DoubleVar] = {}

        # Script tab state
        self.script_video_var = tk.StringVar()
        self.script_edl_var = tk.StringVar()
        self.script_past_dir_var = tk.StringVar()
        self.script_output_var = tk.StringVar()

        # Shared
        self.log_queue: queue.Queue[str | None] = queue.Queue()
        self.worker: threading.Thread | None = None

        self._build()
        self.after(100, self._poll_log)

    # ----- Build -------------------------------------------------------

    def _build(self) -> None:
        notebook = ttk.Notebook(self)
        notebook.pack(fill="x", padx=8, pady=(8, 0))

        cut_tab = ttk.Frame(notebook)
        script_tab = ttk.Frame(notebook)
        notebook.add(cut_tab, text="1. 컷편집")
        notebook.add(script_tab, text="2. 대본 생성")
        self._build_cut_tab(cut_tab)
        self._build_script_tab(script_tab)

        # Shared log area
        log_frame = ttk.LabelFrame(self, text="Log / 로그")
        log_frame.pack(fill="both", expand=True, padx=8, pady=8)
        self.log = tk.Text(log_frame, wrap="none", height=14, state="disabled",
                           font=("Consolas", 10))
        self.log.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(log_frame, command=self.log.yview)
        sb.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=sb.set)

        # Status bar
        self.status = tk.StringVar(value="준비됨.")
        ttk.Label(self, textvariable=self.status, anchor="w",
                  relief="sunken").pack(fill="x", side="bottom")

    def _build_cut_tab(self, parent: ttk.Frame) -> None:
        pad = {"padx": 8, "pady": 4}

        row = ttk.Frame(parent)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="Video / 영상", width=14).pack(side="left")
        ttk.Entry(row, textvariable=self.video_var).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(row, text="찾기...", command=self._pick_video).pack(side="left")

        row = ttk.Frame(parent)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="Output / 출력 EDL", width=14).pack(side="left")
        ttk.Entry(row, textvariable=self.output_var).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(row, text="저장 위치...", command=self._pick_output).pack(side="left")

        settings = ttk.LabelFrame(parent, text="Settings / 설정")
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

        row = ttk.Frame(parent)
        row.pack(fill="x", **pad)
        self.run_btn = ttk.Button(row, text="실행 (Run)", command=self._run_cut)
        self.run_btn.pack(side="left", padx=4)
        ttk.Button(row, text="기본값으로", command=self._reset).pack(side="left", padx=4)
        ttk.Button(row, text="결과 폴더 열기",
                   command=lambda: self._open_dir(self.output_var.get())).pack(side="left", padx=4)

    def _build_script_tab(self, parent: ttk.Frame) -> None:
        pad = {"padx": 8, "pady": 4}

        row = ttk.Frame(parent)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="Video / 영상", width=14).pack(side="left")
        ttk.Entry(row, textvariable=self.script_video_var).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(row, text="찾기...", command=self._pick_script_video).pack(side="left")

        row = ttk.Frame(parent)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="EDL", width=14).pack(side="left")
        ttk.Entry(row, textvariable=self.script_edl_var).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(row, text="찾기...", command=self._pick_script_edl).pack(side="left")

        row = ttk.Frame(parent)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="과거 대본 폴더", width=14).pack(side="left")
        ttk.Entry(row, textvariable=self.script_past_dir_var).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(row, text="폴더...", command=self._pick_past_scripts_dir).pack(side="left")

        # Multi-line video context
        ctx_frame = ttk.LabelFrame(parent, text="이번 영상 정보 (게임·주제·방향·살리고 싶은 느낌)")
        ctx_frame.pack(fill="both", expand=False, **pad)
        self.script_context_text = tk.Text(ctx_frame, height=6, wrap="word",
                                           font=("Consolas", 10))
        self.script_context_text.pack(fill="both", expand=True, padx=4, pady=4)
        self.script_context_text.insert("1.0",
            "예) 게임: Ready or Not / 분위기: 잠입 위주, 긴장감 살리기\n"
            "    이번 미션 주제: 마약상 거래 현장 급습\n"
            "    톤: 평소처럼 자조적·짧은 농담 섞기\n")

        row = ttk.Frame(parent)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="대본 출력 (.txt)", width=14).pack(side="left")
        ttk.Entry(row, textvariable=self.script_output_var).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(row, text="저장 위치...", command=self._pick_script_output).pack(side="left")

        # Action buttons
        row = ttk.Frame(parent)
        row.pack(fill="x", **pad)
        self.script_check_btn = ttk.Button(row, text="의존성 확인 / 설치",
                                           command=self._check_deps)
        self.script_check_btn.pack(side="left", padx=4)
        self.script_run_btn = ttk.Button(row, text="대본 생성", command=self._run_script)
        self.script_run_btn.pack(side="left", padx=4)
        ttk.Button(row, text="결과 폴더 열기",
                   command=lambda: self._open_dir(self.script_output_var.get())).pack(side="left", padx=4)

    # ----- Tooltip -----------------------------------------------------

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

    # ----- File pickers ------------------------------------------------

    def _pick_video(self) -> None:
        path = filedialog.askopenfilename(title="영상 선택", filetypes=VIDEO_TYPES)
        if not path:
            return
        self.video_var.set(path)
        if not self.output_var.get():
            self.output_var.set(str(Path(path).with_suffix(".edl")))

    def _pick_output(self) -> None:
        path = filedialog.asksaveasfilename(
            title="EDL 저장 위치", defaultextension=".edl",
            filetypes=[("EDL", "*.edl"), ("All files", "*.*")],
        )
        if path:
            self.output_var.set(path)

    def _pick_script_video(self) -> None:
        # Auto-fill from cut tab if available
        initial = self.video_var.get() or ""
        path = filedialog.askopenfilename(
            title="영상 선택", filetypes=VIDEO_TYPES,
            initialdir=str(Path(initial).parent) if initial else None,
        )
        if not path:
            return
        self.script_video_var.set(path)
        # Auto-fill EDL and output
        if not self.script_edl_var.get():
            edl = Path(path).with_suffix(".edl")
            if edl.exists():
                self.script_edl_var.set(str(edl))
        if not self.script_output_var.get():
            self.script_output_var.set(str(Path(path).with_name(Path(path).stem + "_script.txt")))

    def _pick_script_edl(self) -> None:
        path = filedialog.askopenfilename(
            title="EDL 선택",
            filetypes=[("EDL", "*.edl"), ("All files", "*.*")],
        )
        if path:
            self.script_edl_var.set(path)

    def _pick_past_scripts_dir(self) -> None:
        path = filedialog.askdirectory(title="과거 대본 폴더 선택")
        if path:
            self.script_past_dir_var.set(path)

    def _pick_script_output(self) -> None:
        path = filedialog.asksaveasfilename(
            title="대본 저장 위치", defaultextension=".txt",
            filetypes=[("Text", "*.txt"), ("All files", "*.*")],
        )
        if path:
            self.script_output_var.set(path)

    def _open_dir(self, path: str) -> None:
        if not path:
            return
        d = Path(path).parent
        if not d.exists():
            return
        if sys.platform == "win32":
            subprocess.Popen(["explorer", str(d)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(d)])
        else:
            subprocess.Popen(["xdg-open", str(d)])

    def _reset(self) -> None:
        for key, _label, default, _tip in SETTINGS:
            self.setting_vars[key].set(default)

    # ----- Log + worker plumbing --------------------------------------

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
        self.script_run_btn.configure(state="normal")
        self.script_check_btn.configure(state="normal")
        self.status.set("완료.")

    def _start_worker(self, fn) -> None:
        self.worker = threading.Thread(target=fn, daemon=True)
        self.worker.start()

    def _log(self, msg: str) -> None:
        self.log_queue.put(msg)

    # ----- Cut pipeline -----------------------------------------------

    def _run_cut(self) -> None:
        video = self.video_var.get().strip()
        output = self.output_var.get().strip()
        if not video:
            messagebox.showerror("auto-cut", "먼저 영상을 선택하세요.")
            return
        if not Path(video).exists():
            messagebox.showerror("auto-cut", f"영상을 찾을 수 없습니다:\n{video}")
            return
        if not output:
            output = str(Path(video).with_suffix(".edl"))
            self.output_var.set(output)

        try:
            config = Config(**{k: float(v.get()) for k, v in self.setting_vars.items()})
        except (tk.TclError, ValueError) as e:
            messagebox.showerror("auto-cut", f"잘못된 설정 값:\n{e}")
            return

        self.run_btn.configure(state="disabled")
        self.status.set("실행 중...")
        self._append_log(f"\n=== 컷편집: {Path(video).name} ===")

        def worker() -> None:
            try:
                run_pipeline(Path(video), Path(output), config, log=self._log)
            except Exception as e:  # noqa: BLE001
                self._log(f"오류: {e}")
            finally:
                self.log_queue.put(None)

        self._start_worker(worker)

    # ----- Script pipeline --------------------------------------------

    def _check_deps(self) -> None:
        self.script_check_btn.configure(state="disabled")
        self._append_log("\n=== 의존성 확인 ===")

        def worker() -> None:
            node = auto_script.check_node()
            claude = auto_script.check_claude()
            self._log(f"  Node.js : {node or '없음'}")
            self._log(f"  Claude  : {claude or '없음'}")

            missing = []
            if not node:
                missing.append("Node.js")
            if not claude:
                missing.append("Claude Code")

            if not missing:
                self._log("→ 모두 설치되어 있음. 대본 생성 가능.")
                self.log_queue.put(None)
                return

            self._log(f"→ 누락: {', '.join(missing)}")

            # Ask user via main thread
            def ask_install():
                ok = messagebox.askyesno(
                    "의존성 설치",
                    f"누락된 의존성: {', '.join(missing)}\n\n"
                    "지금 설치할까요?\n"
                    "(Node.js는 winget UAC 창이 뜰 수 있음. 설치 후 GUI 재시작 필요할 수 있음.)"
                )
                if ok:
                    self._start_worker(self._install_deps_worker)
                else:
                    self._log("→ 설치 취소됨.")
                    self.log_queue.put(None)

            self.after(0, ask_install)

        self._start_worker(worker)

    def _install_deps_worker(self) -> None:
        try:
            if not auto_script.check_node():
                self._log("\n--- Node.js 설치 ---")
                if not auto_script.install_nodejs(self._log):
                    self._log("Node.js 설치 실패.")
                    return
            if not auto_script.check_claude():
                self._log("\n--- Claude Code 설치 ---")
                if not auto_script.install_claude_code(self._log):
                    self._log("Claude Code 설치 실패.")
                    return
            self._log("\n→ 모든 의존성 설치 완료.")
            self._log("⚠ 처음 한 번은 별도 터미널에서 `claude` 직접 실행해서 로그인하세요.")
        finally:
            self.log_queue.put(None)

    def _run_script(self) -> None:
        video = self.script_video_var.get().strip()
        edl = self.script_edl_var.get().strip()
        output = self.script_output_var.get().strip()
        past_dir = self.script_past_dir_var.get().strip()
        context = self.script_context_text.get("1.0", "end").strip()

        if not video or not Path(video).exists():
            messagebox.showerror("auto-cut", "영상 파일을 다시 확인하세요.")
            return
        if not edl or not Path(edl).exists():
            messagebox.showerror("auto-cut", "EDL 파일을 다시 확인하세요.")
            return
        if not output:
            output = str(Path(video).with_name(Path(video).stem + "_script.txt"))
            self.script_output_var.set(output)

        # Quick dependency check
        if auto_script.check_claude() is None:
            messagebox.showerror(
                "auto-cut",
                "Claude Code가 설치되지 않음.\n'의존성 확인 / 설치' 버튼으로 먼저 설치하세요."
            )
            return

        self.script_run_btn.configure(state="disabled")
        self.status.set("대본 생성 중...")
        self._append_log(f"\n=== 대본 생성: {Path(video).name} ===")

        config = auto_script.ScriptConfig(
            video=Path(video),
            edl=Path(edl),
            output=Path(output),
            past_scripts_dir=Path(past_dir) if past_dir else None,
            video_context=context,
        )

        def worker() -> None:
            try:
                auto_script.run_script_pipeline(config, log=self._log)
            except Exception as e:  # noqa: BLE001
                self._log(f"오류: {e}")
            finally:
                self.log_queue.put(None)

        self._start_worker(worker)


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()
