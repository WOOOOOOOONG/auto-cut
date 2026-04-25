# auto-cut

게임 녹화 영상에서 교전 구간과 라운드 경계를 자동으로 감지해 DaVinci Resolve용 EDL을 생성한다.

**현재 버전**: v1.2.0 — 1단계: 컷편집 + 2단계: 자동 대본 생성

## 동작 원리

1. ffmpeg로 모노 PCM 오디오 추출 (16kHz)
2. 1초 윈도우 RMS 에너지 계산 → 임계값 넘는 구간 = 교전 후보
3. PySceneDetect로 장면 전환(라운드 리셋, 메뉴) 감지
4. 교전 구간을 장면 경계에 맞춰 자르고 짧은 간격은 병합
5. 너무 짧은 클립 버림, 앞뒤 패딩 추가
6. 총합이 타겟(기본 20분)을 넘으면 에너지 높은 순으로 우선 채택
7. CMX3600 EDL로 저장

## 요구 사항

- Python 3.9+
- ffmpeg / ffprobe (PATH에 등록)

## 설치

```bash
# Ubuntu 24.04 기준 (PEP 668 준수)
sudo apt install ffmpeg python3-venv python3-tk   # tk는 GUI용

git clone https://github.com/WOOOOOOOONG/auto-cut.git
cd auto-cut

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

이후 사용할 때마다 `source .venv/bin/activate` 후 실행.

## 사용법

### GUI (권장)

```bash
python gui.py
```

- 창에서 영상 파일 선택
- 출력 EDL 경로 자동 채워짐 (수정 가능)
- 설정 슬라이더 조정 후 **Run** 버튼
- 로그 영역에 진행 상황 표시
- 완료 후 **Open output folder**로 결과 폴더 열기

### CLI

```bash
python auto_cut.py gameplay.mp4
# → gameplay.edl 생성

python auto_cut.py gameplay.mp4 \
  --output cuts.edl \
  --target-minutes 20 \
  --rms-percentile 70 \
  --pad-before 2 --pad-after 3 \
  --merge-gap 3 \
  --min-clip 5 \
  --scene-threshold 27
```

### 주요 옵션

| 옵션 | 기본값 | 설명 |
|------|--------|------|
| `--target-minutes` | 20 | 최종 영상 목표 길이 (분) |
| `--rms-percentile` | 70 | 교전 판단 임계값 (낮출수록 더 많은 구간 선택) |
| `--min-loud` | 3초 | 이 시간 이상 소리가 지속되어야 교전으로 인정 |
| `--merge-gap` | 3초 | 가까운 클립끼리 병합 |
| `--min-clip` | 5초 | 이보다 짧은 클립은 버림 |
| `--pad-before` / `--pad-after` | 2 / 3초 | 클립 앞뒤 여유 |
| `--scene-threshold` | 27 | 장면 전환 민감도 (낮을수록 많이 잡힘) |

## DaVinci Resolve에서 가져오기

1. 새 프로젝트 → 타임라인 프레임레이트를 원본 영상과 일치시킴 (예: 60fps)
2. 미디어 풀에 원본 영상 임포트
3. `File → Import → Timeline → Pre-Conformed EDL`
4. EDL 선택, 프레임레이트 설정 (영상과 동일하게)
5. 미디어 풀의 클립을 매칭해 타임라인 생성

## 튜닝 팁

- **클립이 너무 적게 잡힌다**: `--rms-percentile 60` 또는 `--min-loud 2`
- **자잘한 컷이 너무 많다**: `--merge-gap 5` 또는 `--min-clip 8`
- **라운드 경계가 무시된다**: `--scene-threshold 20` (더 민감)
- **잘못된 장면 전환이 끼어든다**: `--scene-threshold 35`

## 제약

- 비드롭 프레임 EDL만 생성 (29.97/59.94에서는 장시간 누적 시 약간 어긋날 수 있음 → 다빈치에서 미세조정)
- 단일 비디오 트랙
- 아직 멀티 입력 미지원
