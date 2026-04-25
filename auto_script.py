#!/usr/bin/env python3
"""Auto script generator for game video clips.

Pipeline:
  1. Parse the EDL produced by auto_cut.py.
  2. Extract 2 keyframes per clip via ffmpeg.
  3. Load the user's past scripts as a style reference.
  4. Build a single big prompt and send it to Claude Code (subscription
     mode via the local `claude -p` CLI).
  5. Parse the response and write a per-clip Korean voiceover script
     to a .txt file.

Dependencies are checked at runtime; we never auto-install without
explicit user consent (the GUI gates that with a dialog).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from auto_cut import _ticker, probe


# ---------- Data types ----------------------------------------------------


@dataclass
class Clip:
    num: int
    src_in: float        # source seconds (where in original video)
    src_out: float
    rec_in: float        # record seconds (where on timeline)
    rec_out: float
    keyframe_paths: list[Path] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.src_out - self.src_in


@dataclass
class ScriptConfig:
    video: Path
    edl: Path
    output: Path
    past_scripts_dir: Path | None
    video_context: str


# ---------- EDL parsing ---------------------------------------------------


def tc_to_seconds(tc: str, fps: float) -> float:
    """HH:MM:SS:FF (non-drop-frame) → seconds."""
    h, m, s, f = map(int, tc.split(":"))
    return h * 3600 + m * 60 + s + f / fps


def format_tc(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


_EDL_RE = re.compile(
    r"^\s*(\d{3,})\s+\S+\s+V\s+C\s+"
    r"(\d{2}:\d{2}:\d{2}:\d{2})\s+(\d{2}:\d{2}:\d{2}:\d{2})\s+"
    r"(\d{2}:\d{2}:\d{2}:\d{2})\s+(\d{2}:\d{2}:\d{2}:\d{2})",
    re.MULTILINE,
)


def parse_edl(path: Path, fps: float) -> list[Clip]:
    text = path.read_text(encoding="utf-8")
    return [
        Clip(
            num=int(m.group(1)),
            src_in=tc_to_seconds(m.group(2), fps),
            src_out=tc_to_seconds(m.group(3), fps),
            rec_in=tc_to_seconds(m.group(4), fps),
            rec_out=tc_to_seconds(m.group(5), fps),
        )
        for m in _EDL_RE.finditer(text)
    ]


# ---------- Dependency check / install -----------------------------------


_NODE_DIRS_WIN = [
    r"C:\Program Files\nodejs",
    r"C:\Program Files (x86)\nodejs",
    os.path.expanduser(r"~\AppData\Local\Programs\nodejs"),
]

_NPM_GLOBAL_DIRS_WIN = [
    os.path.expanduser(r"~\AppData\Roaming\npm"),
]


def _which(cmd: str) -> str | None:
    """shutil.which but also probes Windows .cmd/.bat shims."""
    p = shutil.which(cmd)
    if p:
        return p
    if os.name == "nt":
        for ext in (".cmd", ".bat", ".exe"):
            p = shutil.which(cmd + ext)
            if p:
                return p
    return None


def _refresh_node_path() -> None:
    """Prepend Node.js + global-npm dirs to os.environ['PATH'].

    winget installs Node.js into the user/machine PATH, but our running
    Python process won't pick that up until restart. Manually add the
    well-known dirs so subsequent subprocess calls can find node, npm,
    and globally-installed CLIs (claude).
    """
    if os.name != "nt":
        return
    current = os.environ.get("PATH", "")
    parts = current.split(os.pathsep)
    additions = []
    for d in _NODE_DIRS_WIN + _NPM_GLOBAL_DIRS_WIN:
        if d and os.path.isdir(d) and d not in parts:
            additions.append(d)
    if additions:
        os.environ["PATH"] = os.pathsep.join(additions + parts)


def _find_executable(cmd: str, extra_dirs: list[str] | None = None) -> str | None:
    """Find cmd in PATH or in known Node/npm install dirs."""
    p = _which(cmd)
    if p:
        return p
    if os.name == "nt":
        suffixes = ["", ".cmd", ".bat", ".exe"]
        candidates = list(_NODE_DIRS_WIN) + list(_NPM_GLOBAL_DIRS_WIN) + (extra_dirs or [])
        for d in candidates:
            for s in suffixes:
                full = os.path.join(d, cmd + s)
                if os.path.isfile(full):
                    return full
    return None


def check_node() -> str | None:
    _refresh_node_path()
    exe = _find_executable("node")
    if exe is None:
        return None
    try:
        r = subprocess.run(
            [exe, "--version"], capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def check_claude() -> str | None:
    _refresh_node_path()
    exe = _find_executable("claude")
    if exe is None:
        return None
    try:
        r = subprocess.run(
            [exe, "--version"], capture_output=True, text=True, timeout=10,
            shell=(os.name == "nt"),
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def install_nodejs(log) -> bool:
    """Install Node.js LTS via winget. Returns True on success."""
    log("Node.js LTS 설치 중 (winget install OpenJS.NodeJS.LTS)...")
    log("  → 관리자 권한 UAC 창이 뜨면 승인해주세요.")
    try:
        r = subprocess.run(
            [
                "winget", "install", "OpenJS.NodeJS.LTS",
                "--accept-package-agreements",
                "--accept-source-agreements",
                "--silent",
            ],
            capture_output=True, text=True, timeout=900,
        )
        out = (r.stdout or "") + (r.stderr or "")
        for line in out.splitlines()[-15:]:
            log(f"  {line}")
        if r.returncode != 0:
            log(f"  실패 (exit={r.returncode}). 위 로그 확인.")
            return False
        log("Node.js 설치 완료. 새 PATH 반영을 위해 GUI 재시작이 필요할 수 있음.")
        return True
    except Exception as e:  # noqa: BLE001
        log(f"오류: {e}")
        return False


def install_claude_code(log) -> bool:
    """Install Claude Code globally via npm. Returns True on success."""
    _refresh_node_path()
    npm = _find_executable("npm")
    if npm is None:
        log("npm을 찾을 수 없음. Node.js 설치 확인 또는 GUI 재시작 필요.")
        return False
    log(f"Claude Code 설치 중 (npm: {npm})...")
    try:
        r = subprocess.run(
            [npm, "install", "-g", "@anthropic-ai/claude-code"],
            capture_output=True, text=True, timeout=600,
            shell=(os.name == "nt"),
        )
        out = (r.stdout or "") + (r.stderr or "")
        for line in out.splitlines()[-15:]:
            log(f"  {line}")
        if r.returncode != 0:
            log(f"  실패 (exit={r.returncode}).")
            return False
        log("Claude Code 설치 완료.")
        log("⚠ 처음 사용 전에 터미널에서 `claude` 한 번 실행해서 로그인 필요.")
        return True
    except Exception as e:  # noqa: BLE001
        log(f"오류: {e}")
        return False


# ---------- Keyframe extraction ------------------------------------------


def extract_keyframe(video: Path, time_sec: float, output: Path) -> bool:
    try:
        subprocess.run(
            [
                "ffmpeg", "-ss", f"{time_sec:.3f}", "-i", str(video),
                "-frames:v", "1", "-q:v", "3",
                "-y", "-v", "error", str(output),
            ],
            check=True, capture_output=True, timeout=30,
        )
        return output.exists() and output.stat().st_size > 0
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def extract_keyframes_for_clips(
    video: Path, clips: list[Clip], out_dir: Path, log=print
) -> None:
    """2 frames per clip (start+10%, mid) — enough context, half the tokens."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for c in clips:
        offset = min(0.5, c.duration * 0.1)
        start_t = c.src_in + offset
        mid_t = (c.src_in + c.src_out) / 2
        for label, t in [("start", start_t), ("mid", mid_t)]:
            path = out_dir / f"clip{c.num:03d}_{label}.png"
            if extract_keyframe(video, t, path):
                c.keyframe_paths.append(path)


# ---------- Past scripts loading -----------------------------------------


def load_past_scripts(folder: Path | None) -> list[dict]:
    if folder is None or not folder.exists():
        return []
    scripts = []
    for f in sorted(folder.iterdir()):
        if f.suffix.lower() not in (".txt", ".md"):
            continue
        try:
            content = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = f.read_text(encoding="cp949", errors="ignore")
        scripts.append({"name": f.stem, "content": content.strip()})
    return scripts


# ---------- Prompt building ----------------------------------------------


def build_prompt(
    past_scripts: list[dict], video_context: str, clips: list[Clip]
) -> str:
    parts: list[str] = []

    parts.append(
        "# 역할\n"
        "너는 게임 영상 편집자의 보이스오버 대본을 작성하는 어시스턴트다.\n"
        "아래 과거 대본의 톤·문체·유머·말투·언어 사용 패턴을 학습하고 그대로 따라해라.\n"
        "새 영상의 각 컷에 대해 보이스오버 멘트를 작성한다.\n\n"
    )

    if past_scripts:
        parts.append("# 과거 대본 (스타일 참고용 — 톤·말투·유머를 흡수해서 동일하게 사용)\n\n")
        for s in past_scripts:
            parts.append(f"## [{s['name']}]\n{s['content']}\n\n---\n\n")
    else:
        parts.append(
            "# 스타일 참고\n"
            "과거 대본이 제공되지 않았다. 게임 유튜브 톤(유쾌·친근, 약간의 자조, 짧은 문장)으로 작성한다.\n\n"
        )

    parts.append("# 이번 영상 정보\n")
    parts.append((video_context.strip() or "(영상 정보 입력 안 됨)") + "\n\n")

    parts.append(
        "# 컷 목록\n"
        "각 컷마다 키프레임 이미지가 첨부된다. 이미지를 보고 무슨 일이 일어나는지 파악하고, "
        "그 컷에 어울리는 보이스오버 멘트를 위 스타일로 작성해라.\n"
        "연결 흐름이 중요하다. 앞 컷의 멘트와 자연스럽게 이어지도록.\n\n"
    )

    for c in clips:
        parts.append(
            f"## 컷 #{c.num}  영상 {format_tc(c.src_in)} ~ {format_tc(c.src_out)}  "
            f"(길이 {c.duration:.1f}초)\n"
        )
        for kf in c.keyframe_paths:
            parts.append(f"@{kf.absolute().as_posix()}\n")
        parts.append("\n")

    parts.append(
        "# 출력 형식\n"
        "각 컷에 대해 정확히 아래 형식으로만 답해라. 다른 설명·서론·총평 금지:\n\n"
        "```\n"
        "[CUT 1] 첫 컷의 보이스오버 멘트\n"
        "[CUT 2] 두 번째 컷의 보이스오버 멘트\n"
        "...\n"
        "```\n\n"
        "각 멘트 길이는 컷 길이에 맞게 (대략 컷 길이 × 4글자/초). 너무 짧거나 길지 않게.\n"
    )
    return "".join(parts)


# ---------- Claude invocation --------------------------------------------


def call_claude(prompt: str, log=print, timeout: int = 1800) -> str:
    """Spawn `claude -p` and feed prompt via stdin. Returns stdout text."""
    _refresh_node_path()
    claude_exe = _find_executable("claude")
    if claude_exe is None:
        raise RuntimeError("Claude Code 실행 파일을 찾을 수 없습니다.")

    proc = subprocess.Popen(
        [claude_exe, "-p"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        shell=(os.name == "nt"),
    )
    try:
        stdout, stderr = proc.communicate(input=prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise RuntimeError(f"Claude Code 응답 타임아웃 ({timeout}초)")
    if proc.returncode != 0:
        tail = (stderr or "").strip().splitlines()[-10:]
        raise RuntimeError("Claude Code 오류:\n" + "\n".join(tail))
    return stdout


# ---------- Response parsing & file output ------------------------------


_CUT_RE = re.compile(r"^\s*\[CUT\s*(\d+)\]\s*(.+?)$", re.MULTILINE)


def parse_response(text: str, clips: list[Clip]) -> list[dict]:
    matches = {int(m.group(1)): m.group(2).strip() for m in _CUT_RE.finditer(text)}
    return [
        {
            "num": c.num,
            "src_in": c.src_in,
            "src_out": c.src_out,
            "duration": c.duration,
            "line": matches.get(c.num, "(대본 누락)"),
        }
        for c in clips
    ]


def write_script_file(items: list[dict], video_name: str, output: Path) -> None:
    lines = [f"# 자동 생성 대본 — {video_name}", ""]
    for it in items:
        lines.append(
            f"## [CUT {it['num']}] {format_tc(it['src_in'])} ~ "
            f"{format_tc(it['src_out'])}  ({it['duration']:.1f}s)"
        )
        lines.append(it["line"])
        lines.append("")
    output.write_text("\n".join(lines), encoding="utf-8")


# ---------- Top-level pipeline -------------------------------------------


def run_script_pipeline(config: ScriptConfig, log=print) -> dict:
    log(f"[1/5] EDL 파싱 중: {config.edl.name}")
    _, fps = probe(config.video)
    clips = parse_edl(config.edl, fps)
    log(f"      컷 {len(clips)}개  fps {fps:.3f}")
    if not clips:
        raise RuntimeError("EDL에서 컷을 찾지 못했습니다.")

    with tempfile.TemporaryDirectory(prefix="auto_cut_frames_") as td:
        out_dir = Path(td)
        log("[2/5] 키프레임 추출 중...")
        with _ticker(log, "프레임 추출"):
            extract_keyframes_for_clips(config.video, clips, out_dir, log=log)
        n_frames = sum(len(c.keyframe_paths) for c in clips)
        log(f"      키프레임 {n_frames}장")

        log("[3/5] 과거 대본 로드 중...")
        past = load_past_scripts(config.past_scripts_dir)
        log(f"      과거 대본 {len(past)}편")

        log("[4/5] Claude Code 호출 (수 분 소요)")
        prompt = build_prompt(past, config.video_context, clips)
        with _ticker(log, "Claude 응답 대기", interval=10.0):
            response = call_claude(prompt, log=log)
        log(f"      응답 {len(response)}자 수신")

        log("[5/5] 대본 저장 중...")
        items = parse_response(response, clips)
        write_script_file(items, config.video.name, config.output)
        log(f"      → 저장: {config.output}")

    return {
        "clips": len(clips),
        "scripts_written": sum(1 for it in items if it["line"] != "(대본 누락)"),
        "output": str(config.output),
    }
