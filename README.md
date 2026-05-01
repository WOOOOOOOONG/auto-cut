# auto-cut

> 게임 녹화 영상을 던지면, **하이라이트만 잘라낸 가편집본 + 한국어 보이스오버 대본**을 만들어주는 도구.

마이크 없이 게임만 녹화하고, 나중에 대본 짜고 녹음해서 영상으로 만드는 사람들을 위한 두 단계 자동화 파이프라인.

- **1단계 — 컷편집**: 1~2시간짜리 원본에서 교전·이벤트 구간만 잡아 DaVinci Resolve용 EDL 생성
- **2단계 — 대본 생성**: 잘라낸 컷에서 키프레임을 뽑아 Claude에게 보여주고, **너의 과거 대본 스타일대로** 컷별 보이스오버 멘트를 자동 작성

---

## 누구를 위한 도구인가

- 마이크 없이 게임 녹화 → 후녹음 워크플로 쓰는 사람
- 1~2시간 원본을 매번 손으로 자르는 게 지겨운 사람
- DaVinci Resolve 사용자
- **Claude Pro / Max 구독자** (2단계만 해당, 1단계는 무료)

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

## 한눈에 보기

| 단계 | 입력 | 출력 | 외부 의존 | 비용 |
|------|------|------|-----------|------|
| 1단계 — 컷편집 | 영상 1개 | `.edl` | ffmpeg | 무료 |
| 2단계 — 대본 생성 | 영상 + EDL + 과거 대본 폴더 | `.md`, `.srt` | Claude Code (CLI) | Claude 구독 한도 내 (추가 토큰 비용 0) |

---

## 시작하기

> ⚠ **Windows 전용**으로 테스트되었습니다. (winget 자동 설치, `pythonw.exe`, `.lnk` 바로가기 등 Windows 의존)

### 1. 사전 요구사항

- Python 3.10+ ([python.org](https://www.python.org/downloads/))
- ffmpeg ([gyan.dev](https://www.gyan.dev/ffmpeg/builds/) 또는 `winget install Gyan.FFmpeg`)
- DaVinci Resolve (무료 버전 OK)
- **2단계만**: Claude Pro / Max 구독

### 2. 설치

```powershell
# 1. 클론
git clone https://github.com/WOOOOOOOONG/auto-cut.git
cd auto-cut

# 2. venv 생성
python -m venv .venv
.venv\Scripts\Activate.ps1

# 3. 의존성 설치
pip install -r requirements.txt
```

Node.js와 Claude Code는 GUI에서 첫 실행 시 자동 설치됨 (동의 다이얼로그 거쳐서).

### 3. 바탕화면 바로가기 만들기 (선택)

매번 터미널 켜기 귀찮으면 PowerShell로 한 번 실행:

```powershell
$WshShell = New-Object -ComObject WScript.Shell
$sc = $WshShell.CreateShortcut("$env:USERPROFILE\Desktop\auto-cut.lnk")
$sc.TargetPath = "$PWD\.venv\Scripts\pythonw.exe"
$sc.Arguments = "gui.py"
$sc.WorkingDirectory = "$PWD"
$sc.Save()
```

이후 바탕화면 아이콘 더블클릭 → GUI 실행.

---

## 사용법

### 1단계: 컷편집

GUI 실행 → **`1. 컷편집`** 탭

1. **Video** — 원본 영상 파일 선택
2. **Output** — `.edl` 저장 경로 (기본: 영상과 같은 폴더)
3. 필요하면 설정 조정 (라벨 위에 마우스 올리면 한국어 설명 뜸)
4. **실행 (Run)** 클릭
5. 완료 후 EDL 파일 생성됨

소요 시간: 1시간 영상 기준 보통 2~5분 (장면 감지가 대부분)

### 2단계: 대본 생성

> Claude Code CLI가 필요합니다. 첫 실행 시 GUI에서 자동 설치 안내.

GUI 실행 → **`2. 대본 생성`** 탭

1. **의존성 확인 / 설치** 한 번 클릭
   - Node.js + Claude Code 자동 설치 (동의 필요)
   - 설치 후 별도 터미널에서 `claude` 실행 → Pro/Max 계정 로그인 (1회)
2. **Video** / **EDL** — 1단계 결과물 또는 직접 선택
3. **과거 대본 폴더** — 너가 예전에 쓴 대본들 (`.txt` 또는 `.md`)
   - PDF만 있으면 `PDF → TXT 변환` 버튼으로 일괄 변환
4. **이번 영상 정보** — 게임·주제·톤 등 채워 넣기 (템플릿 제공됨)
5. **출력 형식** — 노션 마크다운(`.md`), SRT 자막(`.srt`), 또는 둘 다
6. **대본 생성** 클릭
7. 완료 후 출력 파일 생성됨

소요 시간: 컷 30~50개 기준 5~15분 (Claude 응답 대기가 대부분)

---

## DaVinci Resolve에서 EDL 가져오기

1. **새 프로젝트** → Project Settings (`Shift+9`)
   - **Timeline frame rate**를 원본 영상 fps와 동일하게 (예: 60fps)
2. 원본 영상을 **Media Pool에 드래그**
3. `File → Import → Timeline...` (`Ctrl+Shift+I`)
4. `.edl` 파일 선택
5. 다이얼로그:
   - **Frame rate**: 위와 동일하게
   - **자동으로 소스 클립을 미디어 풀 가져오기** ✅
   - **일치할 때 파일 확장자 무시** ✅
6. 확인 → 새 타임라인이 Media Pool에 생성됨

SRT 자막은 별도로 `File → Import → Subtitle`로 가져와서 자막 트랙에 올림.

---

## 옵션 가이드 (1단계 컷편집)

| 옵션 | 기본값 | 의미 |
|------|--------|------|
| Target length (min) | 20 | 최종 영상 목표 길이 |
| RMS percentile | 70 | 교전 임계값. 낮출수록 더 많은 구간 (60: 잔잔한 부분도, 80: 폭발만) |
| Min loud (sec) | 3 | 이 시간 이상 시끄러워야 교전으로 인정 |
| Merge gap (sec) | 3 | 가까운 클립 병합 |
| Min clip (sec) | 5 | 이보다 짧은 클립은 버림 |
| Pad before / after | 2 / 3 | 컷 앞/뒤 여유 |
| Scene threshold | 27 | 장면 전환 민감도 (낮을수록 많이) |

### 튜닝 팁

| 증상 | 해결 |
|------|------|
| 클립이 너무 적게 잡힘 | `RMS percentile` ↓ (60), `Min loud` ↓ (2) |
| 자잘한 컷이 너무 많음 | `Merge gap` ↑ (5), `Min clip` ↑ (8) |
| 라운드 경계가 무시됨 | `Scene threshold` ↓ (20) |
| 머즐 플래시·화면 흔들림이 장면으로 잡힘 | `Scene threshold` ↑ (35) |
| EDL은 만들어지는데 다빈치에서 빈 타임라인 | 영상의 모든 오디오 트랙이 묵음. 마이크 트랙만 있는 녹화는 게임 사운드 트랙도 켜고 다시 녹화 |

---

## 자주 나오는 문제

**Q. EDL을 다빈치에 임포트했는데 빨간색 (Media Offline)으로 떠요**
A. 원본 영상이 Media Pool에 없어서 매칭 실패. 원본 영상 미디어 풀에 추가 후 다시 임포트.

**Q. 대본 생성 시 "Claude Code 실행 파일을 찾을 수 없습니다"**
A. Node.js 설치 직후엔 같은 GUI 세션에서 PATH 갱신이 안 됨. **GUI 재시작** 후 다시 시도.

**Q. 대본 길이가 너무 짧아요**
A. 과거 대본이 0개 로드되면 일반 톤으로 떨어집니다. 과거 대본 폴더에 `.txt`/`.md`가 있는지 확인. PDF만 있으면 `PDF → TXT 변환` 버튼 먼저.

**Q. ffmpeg가 모든 오디오 트랙을 묵음으로 받아옵니다**
A. 1.1.3+에선 모든 트랙을 자동으로 믹스합니다. 그래도 묵음이면 영상 자체에 게임 사운드 트랙이 없는 경우 (OBS에서 마이크만 녹화 등).

**Q. Mac이나 Linux에서도 되나요?**
A. 1단계(컷편집)는 코드상 호환되지만 테스트되지 않음. 2단계는 Windows 전용 (winget·`pythonw.exe`·`creationflags=NO_WINDOW` 사용). PR 환영.

---

## 알려진 제약

- **Windows 전용** (Mac/Linux 미테스트)
- **CMX3600 비드롭 프레임 EDL만**: 29.97/59.94 영상은 장시간 누적 시 미세 어긋남 가능 → 다빈치에서 보정
- **단일 비디오 트랙 / 단일 영상 입력** (멀티캠 미지원)
- **2단계는 Claude Pro/Max 구독 필수**: API 키 모드 미지원 (구독 활용이 의도된 설계)
- 대본 생성 시 컷마다 키프레임 2장씩 Claude에 전송 → 컷 50개 영상이면 **메시지 당 이미지 100장** → 가끔 응답이 느리거나 잘림 가능

---

## 라이선스

MIT License — 자유롭게 쓰고 수정해도 됩니다. 자세한 건 `LICENSE` 파일.

## 기여

이슈·PR 환영. 특히 환영하는 부분:
- macOS/Linux 호환성
- 다른 NLE(Premiere XML, FCP XMLv1.10) 출력 포맷
- 다른 게임에서의 튜닝 프리셋
- 키프레임 추출 정확도 개선 (현재는 시간 위치 기반)
