# auto-cut

게임 녹화 영상에서 하이라이트만 잘라낸 가편집본 + 한국어 보이스오버 대본을 만들어주는 도구.

마이크 없이 게임만 녹화하고, 나중에 대본 짜고 녹음해서 영상을 만드는 사람을 위한 자동화 파이프라인.

- **1단계 — 컷편집**: 원본에서 교전·이벤트 구간만 잡아 Premire Pro, DaVinci Resolve등에서 사용할 수 있는 EDL 생성
- **2단계 — 대본 생성**: 잘라낸 컷의 키프레임을 Claude에게 보여주고, 과거 대본 스타일대로 컷별 보이스오버 멘트 자동 작성

## 동작 흐름

```
원본 영상 (MKV/MP4, 1~2시간)
        │
        ▼
┌──────────────────────────┐
│  1단계: 컷편집            │  오디오 RMS + 장면 전환 분석
│  (auto_cut)              │  →  EDL 파일
└──────────────────────────┘
        │
        ├────────────►  DaVinci Resolve 임포트 → 가편집본
        │
        ▼
┌──────────────────────────┐
│  2단계: 대본 생성         │  EDL → 컷별 키프레임 → Claude
│  (auto_script)           │  →  .md (노션) + .srt (자막)
└──────────────────────────┘
```

| 단계 | 입력 | 출력 | 외부 의존 | 비용 |
|------|------|------|-----------|------|
| 1단계 — 컷편집 | 영상 1개 | `.edl` | ffmpeg | 무료 |
| 2단계 — 대본 생성 | 영상 + EDL + 과거 대본 폴더 | `.md`, `.srt` | Claude Code (CLI) | Claude 구독 한도 내 |

---

## 시작하기

> ⚠ Windows 전용으로 테스트되었습니다.

### 사전 요구사항

- Python 3.10+
- ffmpeg (`winget install Gyan.FFmpeg`)
- DaVinci Resolve (무료 버전 OK)
- 2단계만: Claude Pro / Max 구독

### 설치

```powershell
git clone https://github.com/WOOOOOOOONG/auto-cut.git
cd auto-cut
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Node.js와 Claude Code는 GUI에서 첫 실행 시 자동 설치됨.

### 바탕화면 바로가기 (선택)

```powershell
$WshShell = New-Object -ComObject WScript.Shell
$sc = $WshShell.CreateShortcut("$env:USERPROFILE\Desktop\auto-cut.lnk")
$sc.TargetPath = "$PWD\.venv\Scripts\pythonw.exe"
$sc.Arguments = "gui.py"
$sc.WorkingDirectory = "$PWD"
$sc.Save()
```

---

## 사용법

### 1단계: 컷편집

GUI → **1. 컷편집** 탭

1. 원본 영상 파일 선택
2. EDL 저장 경로 지정 (기본: 영상과 같은 폴더)
3. 설정 조정 (라벨에 마우스 올리면 설명 표시)
4. **실행** 클릭

소요 시간: 1시간 영상 기준 2~5분

### 2단계: 대본 생성

GUI → **2. 대본 생성** 탭

1. **의존성 확인 / 설치** 클릭 → Node.js + Claude Code 자동 설치
   - 설치 후 별도 터미널에서 `claude` 실행 → 계정 로그인 (최초 1회)
2. 영상 / EDL 선택
3. 과거 대본 폴더 지정 (`.txt` 또는 `.md`, PDF만 있으면 변환 버튼 사용)
4. 이번 영상 정보 입력 (게임·주제·톤 등)
5. 출력 형식 선택 → **대본 생성** 클릭

소요 시간: 컷 30~50개 기준 5~15분

---

## DaVinci Resolve에서 EDL 가져오기

1. 새 프로젝트 → Project Settings → Timeline frame rate를 원본 fps와 동일하게
2. 원본 영상을 Media Pool에 드래그
3. `File → Import → Timeline...` → `.edl` 선택
4. 다이얼로그에서:
   - Frame rate: 위와 동일
   - 자동으로 소스 클립을 미디어 풀 가져오기 ✅
   - 일치할 때 파일 확장자 무시 ✅

SRT 자막은 `File → Import → Subtitle`로 별도 임포트.

---

## 옵션 가이드 (1단계)

| 옵션 | 기본값 | 의미 |
|------|--------|------|
| Target length (min) | 20 | 최종 영상 목표 길이 |
| RMS percentile | 70 | 교전 임계값 (낮을수록 더 많은 구간 포함) |
| Min loud (sec) | 3 | 이 시간 이상 시끄러워야 교전으로 인정 |
| Merge gap (sec) | 3 | 가까운 클립 병합 간격 |
| Min clip (sec) | 5 | 이보다 짧은 클립은 버림 |
| Pad before / after | 2 / 3 | 컷 앞/뒤 여유 시간 |
| Scene threshold | 27 | 장면 전환 민감도 (낮을수록 많이 감지) |

### 튜닝 팁

| 증상 | 해결 |
|------|------|
| 클립이 너무 적게 잡힘 | RMS percentile ↓ (60), Min loud ↓ (2) |
| 자잘한 컷이 너무 많음 | Merge gap ↑ (5), Min clip ↑ (8) |
| 라운드 경계가 무시됨 | Scene threshold ↓ (20) |
| 머즐 플래시 등이 장면으로 잡힘 | Scene threshold ↑ (35) |
| EDL은 생성됐는데 다빈치에서 빈 타임라인 | 영상의 오디오 트랙이 모두 묵음일 수 있음. 게임 사운드 트랙 포함해서 재녹화 |

---

## 자주 나오는 문제

**EDL 임포트 후 빨간색 (Media Offline)**
→ 원본 영상이 Media Pool에 없어서 매칭 실패. 원본을 Media Pool에 추가 후 다시 임포트.

**"Claude Code 실행 파일을 찾을 수 없습니다"**
→ Node.js 설치 직후 PATH가 갱신 안 됨. GUI 재시작 후 다시 시도.

**대본 길이가 너무 짧음**
→ 과거 대본이 0개 로드되면 일반 톤으로 생성됨. 폴더에 `.txt`/`.md` 파일이 있는지 확인.

**ffmpeg가 오디오를 묵음으로 읽음**
→ v1.1.3+에서는 모든 트랙을 자동 믹스함. 그래도 묵음이면 영상 자체에 게임 사운드가 없는 경우.

---

## 알려진 제약

- Windows 전용 (Mac/Linux 미테스트)
- 29.97/59.94 fps 영상은 장시간 누적 시 미세한 타임코드 어긋남 가능
- 단일 영상 입력만 지원 (멀티캠 미지원)
- 2단계는 Claude Pro/Max 구독 필수

---

## 라이선스

MIT — 자유롭게 사용·수정 가능. 이슈·PR 환영.
