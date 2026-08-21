# Design QA — 대시보드 50:50·kpcs 호버

## 비교 대상

- source visual truth:
  - `C:\Users\심민식\AppData\Local\Temp\codex-clipboard-0cf69895-fff3-4b76-8d4f-886d14fcf9cc.png` (1640 × 645): 리스크/APS 영역 구성
  - `C:\Users\심민식\AppData\Local\Temp\codex-clipboard-5319376c-68a8-4f06-aa54-d6c90b421ca9.png` (767 × 426): 넓은 차트와 굵은 막대 참고
- implementation screenshots:
  - `C:\Users\심민식\Documents\ChatGPT\관별 신규 프로그램(aps)\screenshots\대시보드_50대50_kpcs호버.png` (1918 × 1032)
  - `C:\Users\심민식\Documents\ChatGPT\관별 신규 프로그램(aps)\screenshots\대시보드_필요수량_호버.png` (800 × 800, focused hover state)
- same-input comparison:
  - `C:\Users\심민식\Documents\ChatGPT\관별 신규 프로그램(aps)\screenshots\qa_50대50_kpcs_hover_compare.png` (1920 × 1148)
- viewport: PySide6 native window 1918 × 1032, device scale factor 1
- density normalization: 각 원본은 왜곡 없이 셀 안에 aspect-fit하여 동일 비교 이미지에 배치했다.
- state: S관 고정, 리스크 해외만 선택, APS 국내·해외 선택/안전재고 제외, 필요수량 막대 hover.

## Findings

- 실행 가능한 P0/P1/P2 차이는 남아 있지 않다.
- Fonts and typography: 실제 Windows 실행은 기존 앱의 Malgun Gothic 계층을 유지한다. Qt offscreen 캡처에서 한글 글리프가 누락되는 현상은 기존 실제 Windows 캡처에서 정상 렌더링됨을 확인한 환경 차이다.
- Spacing and layout rhythm: 상단과 하단의 좌우 카드는 각각 1:1 stretch를 사용한다. 카드 사이 간격은 12px로 유지하고 그래프가 카드 내부 가용 폭을 사용한다.
- Colors and visual tokens: 기존 파랑(Clear), 보라(Color), 옅은 회색 그리드와 카드 토큰을 유지한다. 호버는 흰색 배경과 회색 테두리로 컨트롤타워 카드 계열에 맞췄다.
- Image quality and asset fidelity: 기존 똑딱이 로봇 자산을 그대로 사용했으며 대체 이미지나 임시 도형은 없다.
- Copy and content: 리스크 호버는 수주·이니셜·구분·신규분류요약·납기·필요수량을 표시한다. 막대 호버는 Color 다음 Clear 순서로 신규분류요약별 수량을 kpcs로 표시한다.

## Focused region evidence

- 전체 화면에서 리스크 카드와 APS 카드가 같은 폭인지 확인했다.
- 막대 영역 focused capture에서 Color/Clear 합계와 신규분류요약별 kpcs 상세가 표시되는지 확인했다.
- 중요한 상호작용과 숫자 단위가 작아 전체 화면만으로 판단하기 어려워 별도 hover 캡처를 사용했다.

## Comparison history

1. [P1] 상단/하단 좌우 카드가 38:62여서 사용자가 요청한 정중앙 이등분과 달랐다.
   - Fix: 두 행의 stretch를 모두 `1, 1`로 변경했다.
   - Post-fix evidence: `대시보드_50대50_kpcs호버.png`에서 좌측 799px, 우측 798px로 확인했다.
2. [P2] 필요수량 막대가 카드 폭 대비 가늘고 그래프가 충분히 차지 않았다.
   - Fix: 막대 최대 폭을 62px로 늘리고 차트의 좌우 여백을 조정했다.
   - Post-fix evidence: 같은 전체 화면 캡처와 비교 이미지에서 5개 막대가 넓은 카드 폭을 고르게 사용한다.
3. [P1] 리스크 및 필요수량 막대에서 상세 근거를 즉시 확인할 수 없었다.
   - Fix: 리스크 카드와 자식 위젯에 컨트롤타워형 tooltip을 연결하고, 막대 hit area에 Color/Clear 신규분류별 kpcs tooltip을 추가했다.
   - Post-fix evidence: `대시보드_필요수량_호버.png`에서 사출 공정의 Color/Clear 분류별 수량이 표시된다.

## Interaction checks

- 리스크 카드와 카드 안의 라벨 어디에 마우스를 올려도 동일한 상세 tooltip이 열린다.
- 필요수량의 5개 공정 막대 hover hit area가 생성되며 분류별 수량 tooltip이 열린다.
- APS 국내/해외/안전재고 필터 변경 시 표, 막대 합계, tooltip 상세가 함께 갱신된다.
- 필터별 tooltip 분류 합계와 그래프의 Clear/Color 막대값이 일치한다.
- 기존 리스크 클릭 상세, 바깥 클릭/Esc 닫기, 생산수량/수율 전환을 유지한다.
- PySide6 오프스크린 렌더에서 콘솔 오류 없이 캡처했다.

## Follow-up polish

- P3: 신규분류가 매우 많아질 경우 tooltip 높이를 제한하고 스크롤 가능한 팝오버로 확장할 수 있다.

## Implementation checklist

- [x] 좌우 카드 50:50
- [x] 차트 가용 폭 확대 및 막대 굵기 확대
- [x] 축 숫자 kpcs 표시
- [x] 리스크 카드 상세 hover
- [x] Color → Clear 신규분류별 필요수량 hover
- [x] APS 필터와 tooltip 상세 연동

## 수주 상세 전환·월별 생산실적 iteration

- source visual truth:
  - `C:\Users\심민식\AppData\Local\Temp\codex-clipboard-3a177ca2-c5c9-49bc-8801-12589d8551db.png` (781 × 288): 컨트롤타워 수주 상세 2열 정보 구조
  - `C:\Users\심민식\AppData\Local\Temp\codex-clipboard-facb9dfd-103a-4a90-892f-093635da3755.png` (790 × 382): 수율·신규분류 실적 표
  - `C:\Users\심민식\AppData\Local\Temp\codex-clipboard-8bb25b67-8544-4764-89c8-9d357512b314.png` (818 × 387): 생산실적 그래프 카드
- implementation screenshots:
  - `C:\Users\심민식\Documents\ChatGPT\관별 신규 프로그램(aps)\screenshots\수주상세_고정전환_2열정보.png` (1676 × 900)
  - `C:\Users\심민식\Documents\ChatGPT\관별 신규 프로그램(aps)\screenshots\대시보드_당월_일자별생산.png` (1676 × 900)
  - `C:\Users\심민식\Documents\ChatGPT\관별 신규 프로그램(aps)\screenshots\대시보드_당월_공정별추이.png` (1676 × 900)
- same-input comparisons:
  - `C:\Users\심민식\Documents\ChatGPT\관별 신규 프로그램(aps)\screenshots\qa_수주상세_고정전환_비교.png`
  - `C:\Users\심민식\Documents\ChatGPT\관별 신규 프로그램(aps)\screenshots\qa_월선택_일자별공정별_비교.png`
- viewport: PySide6 native window 1676 × 900, device scale factor 1.
- density normalization: 각 원본과 구현 화면을 왜곡 없이 aspect-fit하여 동일 비교 이미지에 배치했다.
- state: 해외 리스크 첫 카드 선택 상세, 당월 일자별 막대, 당월 공정별 꺾은선.

### Findings and fixes

1. [P1] 상세 조회가 끝난 뒤 페이지를 바꾸어 조회 순간에 오른쪽 요약 그래프가 노출될 수 있었다.
   - Fix: 상세 패널을 먼저 고정하고 선택 상태와 로딩 문구를 적용한 뒤 SQLite 상세 조회를 실행한다.
   - Post-fix evidence: 서비스 호출 시점에 `DashboardRightStack`이 이미 상세 패널인지 자동 검증했고 `True`를 확인했다.
2. [P2] 수주 기본정보가 한 줄 텍스트로 밀집되어 컨트롤타워 참고 화면보다 스캔하기 어려웠다.
   - Fix: 납기일/거래처, 이니셜/국가, 구분/공장을 2열 label-value 카드로 재구성했다.
3. [P1] 월 선택과 일별 생산 추이가 없어 당월 진행률과 전월 비교가 어려웠다.
   - Fix: 당월/전월 단일 선택 버튼을 표와 그래프에 공통 연결하고, 월 1일에는 전월이 기본이 되도록 했다.
   - Fix: 일자별은 1일~말일 x축의 생산수량 막대, 공정별은 5개 공정의 일별 꺾은선으로 구현했다.
4. [P2] 상단 상태 문구에 고정 관과 생산실적 기간이 섞여 핵심 상태가 분산됐다.
   - Fix: `APS 갱신`과 컨트롤타워형 `API 전체 양호` 상태만 남겼다.

### Required fidelity surfaces

- Fonts/typography: 기존 Malgun Gothic 크기·굵기 계층을 유지하고 정보 label은 11px, value는 12px bold로 분리했다.
- Spacing/layout rhythm: 상세 정보는 동일 간격 2열 3행이며, 그래프 x축은 31일 전체 폭을 사용한다.
- Colors/tokens: 기존 파랑/초록/보라/주황/청록 공정색과 흰 카드·회색 그리드 토큰을 유지했다.
- Image quality/assets: 기존 똑딱이 로봇 원본 자산을 유지했고 대체 이미지가 없다.
- Copy/content: `S관 고정`과 생산실적 기간 문구를 제거하고 APS 갱신/API 상태, 월 기준, 일자별/공정별 모드를 명확히 표시한다.

### Interaction checks

- 상세 DB 조회 시작 전에 상세 패널이 활성화됨을 검증했다.
- 선택 리스크 카드 1개만 파란 테두리이며 닫기 후 선택이 0개로 돌아간다.
- 당월 → 전월 전환 시 표 행 수, 월 배지, 일별 합계가 함께 변경된다.
- 당월 31개/전월 31개 x축을 생성하고 당월은 데이터가 있는 18일까지 막대·선이 채워진다.
- 일자별 → 공정별 전환 시 막대에서 5개 공정 꺾은선으로 변경된다.
- 월별 일자 합계와 월별 공정 생산 합계가 각각 정확히 일치한다.
- 컴파일 및 PySide6 오프스크린 렌더에서 오류가 없다.

### Remaining findings

- 실행 가능한 P0/P1/P2 차이는 남아 있지 않다.
- P3: 공정별 선 그래프의 범례를 카드 폭이 더 넓을 때 공정색별 개별 범례로 확장할 수 있다.

## 리스크 press 전환·가로 누적 필요수량 iteration

- source visual truth: `C:\Users\심민식\AppData\Local\Temp\codex-clipboard-65a224a6-8723-4afd-a432-e89820724a30.png` (850 × 512)
- implementation screenshots:
  - `C:\Users\심민식\Documents\ChatGPT\관별 신규 프로그램(aps)\screenshots\대시보드_가로누적_필요수량.png` (1676 × 900)
  - `C:\Users\심민식\Documents\ChatGPT\관별 신규 프로그램(aps)\screenshots\대시보드_가로누적_간소호버.png` (1676 × 900)
- same-input comparison: `C:\Users\심민식\Documents\ChatGPT\관별 신규 프로그램(aps)\screenshots\qa_가로누적_필요수량_비교.png` (1920 × 736)
- viewport: PySide6 native window 1676 × 900, device scale factor 1.
- state: 국내·해외 포함/안전재고 제외, 가로 누적 필요수량, 해외 리스크 카드 mouse press.

### Findings and fixes

1. [P1] 자식 라벨 위에서 좌클릭을 누르고 있는 동안 상세가 열리지 않고 그래프가 보였다.
   - Fix: 애플리케이션 event filter에서 리스크 카드 및 자식 위젯의 left mouse press를 먼저 가로채 상세를 즉시 연다.
   - Fix: 해당 press 이벤트를 소비해 부모 스크롤 영역으로 전파되며 상세가 다시 닫히는 경로를 차단했다.
   - Post-fix evidence: 카드 본체와 자식 라벨 각각을 1초간 누른 테스트에서 press/hold/release 전 상태가 모두 상세 패널로 유지됐다.
2. [P2] 세로 누적 막대는 공정명이 하단에 있고 큰 tooltip이 그래프를 가렸다.
   - Fix: 5개 공정을 고정 순서의 가로 누적 막대로 변경했다.
   - Fix: Clear/Color 구간 kpcs를 막대 내부에, 총 kpcs를 막대 끝에 상시 표시했다.
   - Fix: hover는 Color/Clear별 상위 3개 신규분류와 나머지 개수만 표시하도록 축약했다.

### Required fidelity surfaces

- Fonts/typography: 기존 Malgun Gothic 계층과 kpcs 표기 규칙을 유지했다.
- Spacing/layout rhythm: 공정명 왼쪽, 누적 막대 중앙, 총량 오른쪽의 3단 읽기 흐름으로 정렬했다.
- Colors/tokens: Clear 파랑과 Color 보라 및 기존 카드/그리드 토큰을 유지했다.
- Image quality/assets: 기존 똑딱이 로봇 원본 자산을 유지했으며 새 이미지 대체가 없다.
- Copy/content: 공정 순서와 실제 APS 합계를 유지하고 tooltip 상세만 상위 항목으로 축약했다.

### Interaction checks

- 카드 본체 press → 1초 hold → release 동안 상세 패널 유지.
- 카드 내부 QLabel press → 1초 hold → release 동안 상세 패널 유지.
- 5개 가로 막대 총량이 APS 표의 공정별 필요수량과 일치한다.
- 사출 hover의 `Color 850.0 + Clear 2,571.7 = 총 3,421.7 kpcs`가 일치한다.
- 컴파일 및 PySide6 오프스크린 렌더에서 오류가 없다.

### Remaining findings

- 실행 가능한 P0/P1/P2 차이는 남아 있지 않다.
- P3: 창 폭이 매우 좁으면 막대 내부 구간 수량을 자동 숨기는 반응형 규칙을 추가할 수 있다.

## 공정색상·둥근 트랙 필요수량 iteration

- source visual truth: `C:\Users\심민식\AppData\Local\Temp\codex-clipboard-6f4af911-6e92-41bf-bbc6-99b1caa0c863.png` (817 × 306)
- implementation screenshot: `C:\Users\심민식\Documents\ChatGPT\관별 신규 프로그램(aps)\screenshots\대시보드_공정색상_트랙형필요수량.png` (1676 × 900)
- same-input comparison: `C:\Users\심민식\Documents\ChatGPT\관별 신규 프로그램(aps)\screenshots\qa_공정색상_트랙형_비교.png` (1920 × 736)
- viewport: PySide6 native window 1676 × 900, device scale factor 1.
- state: 국내·해외 포함/안전재고 제외, 공정별 가로 누적 필요수량.

### Findings and fixes

1. [P2] 모든 공정이 같은 파랑/보라 조합이어서 행을 빠르게 구분하기 어려웠다.
   - Fix: 사출 파랑, 분리 초록, 하이드레이션 보라, 검사·접착 주황, 누수·규격 청록을 적용했다.
2. [P2] 막대가 기준 없이 떠 보여 참고 화면의 진행 트랙형 시인성이 부족했다.
   - Fix: 최대 필요수량을 기준으로 한 둥근 회색 트랙을 추가하고 실제 합계를 그 위에 채웠다.
   - Fix: 공정별 진한색은 Clear, 연한색은 Color로 일관되게 구분했다.
   - 목표/예상 데이터는 현재 소스에 없으므로 참고 화면의 목표선은 의도적으로 제외했다.

### Required fidelity surfaces

- Fonts/typography: 기존 Malgun Gothic과 kpcs 숫자 굵기를 유지했다.
- Spacing/layout rhythm: 공정명-트랙-총량의 한 행 구조와 5개 행의 동일 간격을 유지했다.
- Colors/tokens: 5개 공정 고유색과 동일 색상의 명도 차이를 사용하며 회색 트랙을 추가했다.
- Image quality/assets: 기존 똑딱이 자산을 유지했고 새 이미지 대체가 없다.
- Copy/content: 범례를 `진한색 Clear · 연한색 Color · 회색 트랙 최대 공정 기준`으로 변경했다.

### Interaction checks

- 5개 공정의 Clear/Color/총량 값은 이전 APS 집계와 동일하다.
- 간소화 hover와 필터 연동을 유지한다.
- 리스크 카드 자식 라벨 1초 press/hold/release 상세 유지 테스트를 재통과했다.
- 컴파일 및 PySide6 오프스크린 렌더에서 오류가 없다.

### Remaining findings

- 실행 가능한 P0/P1/P2 차이는 남아 있지 않다.
- P3: 향후 기준 목표나 공정 능력 데이터가 연결되면 참고 화면처럼 목표선을 추가할 수 있다.

## 소구간 메모 표시 iteration

- source visual truth:
  - `C:\Users\심민식\AppData\Local\Temp\codex-clipboard-46d663ae-ddf2-4709-a5ac-7be3ba5ec6ff.png` (690 × 286): 좁은 Color 구간의 값이 생략된 상태
  - `C:\Users\심민식\AppData\Local\Temp\codex-clipboard-b60e796b-1e63-484f-9adb-dadbbae8d57d.png` (265 × 100): Excel 메모형 연결선 참고
- implementation screenshot: `C:\Users\심민식\Documents\ChatGPT\관별 신규 프로그램(aps)\screenshots\필요수량_소구간_메모표시.png` (621 × 230)
- same-input comparison: `C:\Users\심민식\Documents\ChatGPT\관별 신규 프로그램(aps)\screenshots\qa_소구간_메모표시_비교.png` (1500 × 520)
- state: APS 해외만 선택하여 Color 구간이 58px 미만인 실제 데이터 상태.

### Findings and fixes

1. [P1] 폭이 좁은 Color 구간의 수량이 숨겨져 값을 화면에서 확인할 수 없었다.
   - Fix: 구간 폭이 임계값 미만이면 공정색 테두리 메모, 연결선, 기준점으로 수량을 외부 표기한다.
   - Fix: Clear와 Color가 동시에 좁으면 메모 하나로 합쳐 과도한 라벨 겹침을 막는다.
   - Post-fix evidence: 해외 필터 실제 데이터 5개 행에서 Color 67k/78k/74k/80k/81k가 모두 상시 표시된다.

### Required fidelity surfaces

- Fonts/typography: Malgun Gothic 7px bold 메모와 8px bold 막대 수량으로 정보 계층을 유지한다.
- Spacing/layout rhythm: 메모는 해당 막대 바로 위에 배치하고 차트 경계 안으로 자동 보정한다.
- Colors/tokens: 각 공정 고유색을 메모 테두리·연결선·기준점에 재사용한다.
- Image quality/assets: 새 래스터 자산 없이 Qt 네이티브 차트 표기만 추가했다.
- Copy/content: `Color 67k`처럼 렌즈 구분과 kpcs 축약값을 함께 표시한다.

## 누수·규격 양품·종합수율 복합 그래프 iteration

- source visual truth:
  - `C:\Users\심민식\AppData\Local\Temp\codex-clipboard-7fbec63b-5314-41d9-8223-74e5de94d37d.png` (826 × 377): 일자별 막대 구성
  - `C:\Users\심민식\AppData\Local\Temp\codex-clipboard-e35ef015-a266-4b0d-9aa6-baaf921b2b27.png` (806 × 384): 공정별 선 구성
  - Obsidian `프로젝트/SCM Control Tower/생산실적현황 개발 기록 2026-07-24.md::생산 KPI 정의`
- implementation screenshot: `C:\Users\심민식\Documents\ChatGPT\관별 신규 프로그램(aps)\screenshots\대시보드_누수규격양품_종합수율.png` (1676 × 900)
- same-input comparison: `C:\Users\심민식\Documents\ChatGPT\관별 신규 프로그램(aps)\screenshots\qa_누수규격양품_종합수율_비교.png` (1800 × 1000)
- viewport: PySide6 native window 1676 × 900, device scale factor 1.
- density normalization: 원본 2장과 구현 전체 화면을 aspect-fit하여 하나의 비교 이미지에서 확인했다.
- state: 2026-08 당월, 1일~31일, 18일까지 생산실적 존재.

### Findings and fixes

1. [P1] 일자별 막대가 5개 공정 생산량 합계여서 생산 KPI 정의와 달랐다.
   - Fix: 마지막 공정 `누수·규격`의 일별 `pr_qty` 양품수량만 막대로 표시한다.
2. [P1] 공정별 보기 전환은 대시보드에서 최종 산출과 품질 수준을 한 번에 판단하기 어렵게 했다.
   - Fix: 공정별 버튼과 5개 선을 제거하고, 같은 일자 축에 종합수율 한 개 선을 겹쳤다.
3. [P1] 종합수율 정의가 화면 계산에 연결되지 않았다.
   - Fix: 일자·공정별 `양품수량 ÷ 생산수량`을 구한 뒤 사출→분리→하이드레이션→검사·접착→누수·규격의 5개 수율을 연속곱한다.
   - Fix: 한 공정이라도 실적이 없는 날은 0%로 만들지 않고 점과 선을 끊는다.

### Required fidelity surfaces

- Fonts/typography: 기존 카드 제목·설명 계층을 유지하고 범례에서 막대와 선의 의미를 직접 표기한다.
- Spacing/layout rhythm: 단일 카드 안에 왼쪽 kpcs축, 오른쪽 0~100%축, 공통 일자축을 배치했다.
- Colors/tokens: 양품수량은 기존 파랑 막대, 종합수율은 주황 선·흰 원형 마커로 구분한다.
- Image quality/assets: 기존 똑딱이 로봇 원본을 그대로 유지하며 대체 자산이 없다.
- Copy/content: `누수·규격 양품수량`, `종합수율`, `5개 공정 수율의 연속곱`을 명시한다.

### Interaction and data checks

- 당월 18개 실적일에서 누수·규격 양품 막대와 종합수율 18개 값이 생성됐다.
- 2026-08-01 종합수율 69.5% 등 일자별 값은 5개 공정의 양품/생산 연속곱으로 재계산했다.
- 호버에는 날짜, 누수·규격 양품 kpcs, 종합수율, 계산 근거인 5개 공정 수율이 표시된다.
- 당월/전월 선택 시 표·그래프·월 배지가 함께 변경된다.
- 컴파일, 데이터 정합성 검사, PySide6 실제 Windows 렌더 캡처를 통과했다.

### Remaining findings

- 실행 가능한 P0/P1/P2 차이는 남아 있지 않다.
- P3: 데이터가 매우 조밀해지는 장기 기간에는 종합수율 마커를 선택적으로 축소할 수 있다.

## 상단 상태 표시 우측 소형화 iteration

- source visual truth: `C:\Users\심민식\AppData\Local\Temp\codex-clipboard-63087844-ddb8-4d1a-b59a-f3693300dfbe.png` (952 × 80)
- implementation screenshot: `C:\Users\심민식\Documents\ChatGPT\관별 신규 프로그램(aps)\screenshots\대시보드_상태표시_우측소형.png` (1676 × 900)
- same-input comparison: `C:\Users\심민식\Documents\ChatGPT\관별 신규 프로그램(aps)\screenshots\qa_상태표시_우측소형_비교.png` (1700 × 950)
- state: 대시보드, APS 갱신 정상, API 전체 양호.

### Finding and fix

1. [P2] APS 갱신 라벨이 stretch factor 1로 남은 폭 전체를 차지해 상단 공간을 낭비했다.
   - Fix: 라벨을 `Maximum × Fixed` 크기 정책과 stretch 0으로 바꾸고 상태 칩과 함께 우측 정렬했다.
   - Post-fix evidence: APS 라벨 191px, API 상태 115px로 한 행에 유지되며 중앙 여백을 점유하지 않는다.

### Required fidelity surfaces

- Fonts/typography: 기존 11px bold 상태 텍스트를 유지한다.
- Spacing/layout rhythm: 두 상태 요소를 10px 간격의 우측 한 줄 그룹으로 압축했다.
- Colors/tokens: APS 연파랑, 정상 상태 연초록 토큰을 유지한다.
- Image quality/assets: 관련 이미지 자산 변경 없음.
- Copy/content: 갱신 전체 시각과 API 수집 상태를 그대로 보존한다.

## 부족수량·생산현황 명칭 및 시각화 iteration

- source visual truth:
  - `C:\Users\심민식\AppData\Local\Temp\codex-clipboard-4333b5db-b715-473e-bcbf-9f20bf035d96.png` (831 × 163): APS 부족수량 카드
  - `C:\Users\심민식\AppData\Local\Temp\codex-clipboard-5623556e-f410-43b2-851f-74fda0d8b3e0.png` (841 × 377): 생산실적 표
  - `C:\Users\심민식\AppData\Local\Temp\codex-clipboard-6cde1770-d6ab-441a-ab02-b73d129f8c67.png` (828 × 383): 일자별 생산 그래프
- implementation screenshot: `C:\Users\심민식\Documents\ChatGPT\관별 신규 프로그램(aps)\screenshots\대시보드_부족수량표_생산현황개선.png` (1676 × 900)
- same-input comparison: `C:\Users\심민식\Documents\ChatGPT\관별 신규 프로그램(aps)\screenshots\qa_부족수량표_생산현황개선_비교.png` (1900 × 1000)
- viewport: PySide6 native window 1676 × 900, device scale factor 1.
- state: 당월, APS 국내·해외 선택/안전재고 제외.

### Findings and fixes

1. [P2] 회색 트랙이 목표·CAPA·진척 잔량처럼 보였지만 데이터에는 해당 기준이 없었다.
   - Fix: 회색 트랙을 제거하고 Clear+Color 부족수량 누적 막대와 공통 0 기준축만 유지했다.
2. [P2] APS 카드의 `관`과 제목이 사용자 업무 용어와 달랐다.
   - Fix: 컬럼을 `공장`, 카드명을 `공정별 부족수량(APS_S관)`으로 변경했다.
3. [P1] 생산실적 표의 신규분류 행이 양품 생산수량이어서 사용자가 요청한 APS 부족수량과 달랐다.
   - Fix: `Clear 수율/Color 수율`을 `Clear/Color`로 축약했다.
   - Fix: 섹션명을 `신규분류요약별`로 바꾸고 APS 신규분류별 부족수량을 공정별로 합산했다.
   - Post-fix evidence: 국내·해외 기준 분류행 합계가 위 APS 표의 3,421,663 / 3,383,581 / 3,411,452 / 3,872,115 / 3,752,896 pcs와 일치한다.
4. [P2] 생산 그래프의 최종 일자 값과 선의 현재 위치를 빠르게 읽기 어려웠다.
   - Fix: 카드명을 `일자별 생산현황`으로 축약하고 막대 폭·둥근 모서리를 조정했다.
   - Fix: 종합수율 선에 부드러운 외곽선과 흰색 마커를 적용하고 최신 양품 kpcs·종합수율을 직접 표기했다.

### Required fidelity surfaces

- Fonts/typography: 기존 Malgun Gothic 계층을 유지하며 카드명과 표 라벨을 요청 문구로 정확히 변경했다.
- Spacing/layout rhythm: 50:50 카드 폭과 한 행 구성을 유지하며 차트 내부 라벨이 축·막대와 충돌하지 않는다.
- Colors/tokens: 부족수량은 공정별 고유색, 일자별 양품은 파랑, 종합수율은 주황으로 제한했다.
- Image quality/assets: 기존 똑딱이 로봇 원본 자산을 유지했다.
- Copy/content: `공장`, `공정별 부족수량(APS_S관)`, `생산실적`, `신규분류요약별`, `일자별 생산현황`을 반영했다.

### Interaction and data checks

- APS 국내·해외·안전재고 필터가 위 표, 부족수량 막대, 신규분류별 표에 함께 적용된다.
- 해외만 선택한 상태에서도 신규분류 행 합계와 APS 표의 365,596 / 311,001 / 487,944 / 949,364 / 920,225 pcs가 일치한다.
- 당월/전월 선택은 Clear·Color 공정수율과 일자별 생산현황에 계속 연동된다.
- 생산현황 호버에는 누수·규격 양품, 종합수율, 5개 공정별 수율이 유지된다.
- 컴파일, 실제 Windows 렌더, 정합성 검사를 통과했다.

### Remaining findings

- 실행 가능한 P0/P1/P2 차이는 남아 있지 않다.
- P3: 긴 신규분류명은 기존 tooltip으로 전체 명칭을 확인한다.

## 신규분류 업무순서·공정별 부족합계 iteration

- source visual truth:
  - `C:\Users\심민식\AppData\Local\Temp\codex-clipboard-6a26a786-bc98-4a2d-86e2-0940f8ca8d9a.png` (826 × 383): 신규분류요약 목록
  - `C:\Users\심민식\AppData\Local\Temp\codex-clipboard-a8dd7179-0791-47ea-abae-92dd7b7d5f0f.png` (805 × 264): 구분행에 공정별 부족수량을 표시하는 참고
  - Obsidian `프로젝트/SCM Control Tower/원데이 58 재작업 ERP 자동화 및 사용가능수량 API 연동 설계 2026-07-30.md::2026-08-05 SCM 재작업리스트 산출 프로그램 구현::필터와 신규분류 버튼`
- implementation screenshot: `C:\Users\심민식\Documents\ChatGPT\관별 신규 프로그램(aps)\screenshots\생산실적_신규분류정렬_부족합계.png` (645 × 251)
- same-input comparison: `C:\Users\심민식\Documents\ChatGPT\관별 신규 프로그램(aps)\screenshots\qa_신규분류정렬_부족합계_비교.png` (1500 × 650)
- viewport: 생산실적 표 focused capture 645 × 251, device scale factor 1.
- state: APS 국내·해외 선택/안전재고 제외, 당월.

### Findings and fixes

1. [P1] 신규분류 행이 일반 문자열 순서로 보일 가능성이 있어 업무 우선순위가 보장되지 않았다.
   - Fix: Obsidian 확정 순서인 Clear→Color 묶음 안에서 `FRP→1-Day`, `HEMA→Si_`, `Sph→M/F→Toric→Fix→Fix2`를 적용하는 공용 `classification_sort_key`로 고정했다.
   - Post-fix evidence: 현재 데이터는 `Si_FRP_Sph → Si_FRP_M/F → 1-Day_Sph → Si_1-Day_Sph → Si_1-Day_M/F → Si_1-Day_Toric` 이후 Color 묶음 순서로 표시된다.
2. [P2] 신규분류 구분행에 공정별 전체 규모가 없어 아래 세부행의 합계를 즉시 파악하기 어려웠다.
   - Fix: 병합 구분행을 7개 셀로 분리하고 사출·분리·하이드레이션·검사접착·누수규격·종합 부족수량을 빨간 굵은 글씨로 표시했다.
   - Fix: 셀 폭에서 잘리지 않도록 화면은 kpcs로 표시하고, 마우스오버에는 쉼표가 있는 전체 pcs를 제공한다.

### Required fidelity surfaces

- Fonts/typography: 신규분류 구분은 파란 8px bold, 부족합계는 빨간 8px bold로 위계를 분리했다.
- Spacing/layout rhythm: 기존 30px 구분행 높이 안에서 각 합계가 해당 공정 열 중앙에 정렬된다.
- Colors/tokens: 연파랑 구분 배경과 파란 제목을 유지하고 부족합계만 의미색 빨강 `#D92D20`을 사용한다.
- Image quality/assets: 관련 이미지 자산 변경 없음.
- Copy/content: 신규분류명, 공정별 kpcs 합계, 종합 합계를 표시한다.

### Interaction and data checks

- 신규분류 행 공정별 합계는 위 APS 부족수량 표와 동일하다.
- 필터 변경 시 빨간 합계와 아래 신규분류 세부행이 함께 재계산된다.
- 각 빨간 합계 셀 tooltip에서 전체 pcs를 확인할 수 있다.
- 컴파일과 실제 Windows focused 렌더를 통과했다.

### Remaining findings

- 실행 가능한 P0/P1/P2 차이는 남아 있지 않다.

## 일자별 생산현황 끝 지점 직접 라벨 iteration

- source visual truth: `C:\Users\심민식\AppData\Local\Temp\codex-clipboard-78505918-3772-4055-ac13-ab161f1892d5.png` (603 × 356)
- implementation screenshot: `C:\Users\심민식\Documents\ChatGPT\관별 신규 프로그램(aps)\screenshots\대시보드_생산실적_수율_끝라벨.png` (1936 × 1048)
- same-input comparison: `C:\Users\심민식\Documents\ChatGPT\관별 신규 프로그램(aps)\screenshots\qa_생산실적_수율_끝라벨_비교.png` (1600 × 520)
- viewport: Windows 최대화 앱 1936 × 1048, device scale factor 1.
- state: 대시보드, 해외 선택, 안전재고 제외, 2026-08 당월.

### Findings and fixes

1. [P2] 마지막 데이터의 막대값과 수율값이 숫자만 표시되어 지표 의미를 즉시 구분하기 어려웠다.
   - Fix: 마지막 막대 우측에 `생산실적 - 39k pcs`, 마지막 선 우측에 `수율 - 57.6%`를 각 시리즈 색상으로 직접 표시했다.
2. [P2] 목표선과 생산 기준선은 사용자가 최종적으로 삭제를 요청했다.
   - Fix: 모든 목표·기준선과 관련 라벨을 제거하고 실적 막대와 종합수율 선만 유지했다.
3. [P2] `S관`, `Clear`, `Color` 행 머리글이 좌측 정렬되어 표의 숫자 열과 시각축이 달랐다.
   - Fix: 해당 셀을 수평·수직 가운데 정렬했다.

### Required fidelity surfaces

- Fonts/typography: Malgun Gothic과 기존 크기·굵기를 유지하고 끝 라벨만 7–8px bold로 강조했다.
- Spacing/layout rhythm: 두 직접 라벨의 높이를 분리해 서로 겹치지 않고 최신 데이터 오른쪽 빈 공간을 활용한다.
- Colors/tokens: 생산실적은 파랑, 종합수율은 주황으로 기존 범례와 일치한다.
- Image quality/assets: 이미지 자산 변경 없음.
- Copy/content: `생산실적 - …k pcs`, `수율 - …%` 문구를 정확히 반영했다.

### Interaction and data checks

- 1일부터 말일까지의 X축 구조와 최신 데이터 지점 계산을 유지한다.
- 최신 생산실적과 최신 종합수율이 서로 다른 높이에서 직접 표시된다.
- 목표·기준선은 화면에 남아 있지 않다.
- 컴파일, 실제 Windows 렌더링, 앱 응답 상태를 통과했다.

### Remaining findings

- 실행 가능한 P0/P1/P2 차이는 남아 있지 않다.

## 생산실적 신규분류별 월 양품실적 iteration

- source visual truth:
  - `C:\Users\심민식\AppData\Local\Temp\codex-clipboard-c13be650-93e6-4b7f-ac2d-53e4d0e544c1.png` (1674 × 423)
  - `C:\Users\심민식\AppData\Local\Temp\codex-clipboard-f80e2cab-d561-464d-b865-1c1fa3b46770.png` (790 × 94)
- implementation screenshot: `C:\Users\심민식\Documents\ChatGPT\관별 신규 프로그램(aps)\screenshots\생산실적_신규분류별_월양품실적.png` (1936 × 1048)
- same-input comparison: `C:\Users\심민식\Documents\ChatGPT\관별 신규 프로그램(aps)\screenshots\qa_생산실적_신규분류별_월양품실적_비교.png` (1600 × 600)
- viewport: Windows 최대화 앱 1936 × 1048, device scale factor 1.
- state: 대시보드, 당월(2026-08), 해외 기본 선택.

### Findings and fixes

1. [P1] 생산실적 카드의 신규분류별 상세값이 APS 부족수량으로 표시되어 카드 의미와 데이터가 불일치했다.
   - Fix: 선택 월 생산실적 DB의 `pr_qty`를 신규분류요약·공정별로 합산한 `classification_good`을 표의 상세행 데이터로 사용했다.
2. [P2] 신규분류요약 합계가 위험 의미의 빨간색으로 표시되어 생산실적으로 읽히지 않았다.
   - Fix: 합계행을 생산 계열 파랑 `#0A67D1`로 변경하고 tooltip을 `총 양품실적 … pcs`로 수정했다.
3. [P2] 카드 부제에 APS 부족수량이라는 잘못된 설명이 남아 있었다.
   - Fix: `선택 월 Clear·Color 공정수율 · 신규분류요약별 공정 양품실적`로 교체했다.

### Required fidelity surfaces

- Fonts/typography: 기존 Malgun Gothic, 표 밀도와 굵기를 유지했다.
- Spacing/layout rhythm: 기존 행 높이·열 정렬·스크롤 구조를 유지해 정보밀도 변화가 없다.
- Colors/tokens: 생산실적 합계는 파랑, 수율은 기존 중립 텍스트를 유지한다.
- Image quality/assets: 이미지 자산 변경 없음.
- Copy/content: 생산실적, 공정 양품실적, 총 양품실적 문구와 선택 월 의미가 일치한다.

### Interaction and data checks

- 당월 2026-08: 신규분류 11개, 공정별 양품합계 2,949,260 / 2,622,820 / 2,044,316 / 2,206,905 / 2,073,950 pcs.
- 전월 2026-07: 신규분류 9개, 공정별 양품합계 4,914,457 / 4,775,172 / 4,091,034 / 4,520,293 / 4,434,754 pcs.
- 당월·전월 버튼 전환 시 `classification_good`과 공정수율이 같은 기간으로 함께 갱신된다.
- 신규분류 정렬은 기존 업무 정렬키를 유지한다.
- 컴파일, 실제 Windows 렌더링, 앱 응답 상태를 통과했다.

### Remaining findings

- 실행 가능한 P0/P1/P2 차이는 남아 있지 않다.

## 전월 말일 자동 콜아웃·리스크 필터 iteration

- source visual truth:
  - `C:\Users\심민식\AppData\Local\Temp\codex-clipboard-dfc4f186-9365-4517-9e9a-accd37995565.png` (127 × 69)
  - `C:\Users\심민식\AppData\Local\Temp\codex-clipboard-e952c59b-095b-4a93-8d7c-ae87b4c39d0f.png` (808 × 391)
  - `C:\Users\심민식\AppData\Local\Temp\codex-clipboard-5da946e7-37da-4dc2-b8f6-2d0caf13bcdf.png` (244 × 93)
  - `C:\Users\심민식\AppData\Local\Temp\codex-clipboard-7a5dc4fc-23f0-46dd-86a8-78fd45019f87.png` (866 × 619)
  - `C:\Users\심민식\AppData\Local\Temp\codex-clipboard-a3caa85b-5ee5-47e7-85a1-7dcbd9576e61.png` (647 × 91)
- implementation screenshot: `C:\Users\심민식\Documents\ChatGPT\관별 신규 프로그램(aps)\screenshots\대시보드_전월자동콜아웃_리스크필터.png` (1936 × 1048)
- same-input comparison: `C:\Users\심민식\Documents\ChatGPT\관별 신규 프로그램(aps)\screenshots\qa_전월자동콜아웃_리스크필터_비교.png` (1800 × 1050)
- viewport: Windows 최대화 앱 1936 × 1048, device scale factor 1.
- state: 대시보드, 전월(2026-07), 해외 선택.

### Findings and fixes

1. [P1] 전월 말일처럼 마지막 데이터가 우측 끝에 있을 때 생산실적·수율 직접 라벨이 막대와 선 위에 겹쳤다.
   - Fix: 우측 여백이 부족하거나 두 기준점의 세로 간격이 28px 미만이면 흰색 라운드 메모 박스, 연결선, 기준점으로 자동 전환한다.
   - Post-fix evidence: 전월 31일에서 `생산실적 - 135k pcs`와 `수율 - 72.3%`가 서로 다른 높이의 콜아웃으로 표시되어 겹침이 없다.
2. [P2] 리스크 알림에서 안전재고 필터가 불필요하고 전체 건수가 체크 상태에 따라 변했다.
   - Fix: 리스크 카드에서는 안전재고 체크를 제거하고 국내·해외만 유지했다.
   - Fix: `전체`는 국내+해외 전체 건수로 고정하고 `위험·주의` 및 목록·하단 대상 건수만 체크된 구분으로 계산한다.

### Required fidelity surfaces

- Fonts/typography: 메모 박스는 Malgun Gothic 7px bold로 작은 공간에서도 지표명과 값을 함께 읽을 수 있다.
- Spacing/layout rhythm: 생산실적 콜아웃은 기준점 위쪽, 수율 콜아웃은 아래쪽에 배치하고 교차 시 추가 간격을 확보한다.
- Colors/tokens: 생산실적 콜아웃은 파랑, 수율 콜아웃은 주황으로 범례와 일치한다.
- Image quality/assets: 이미지 자산 변경 없음.
- Copy/content: 안전재고 제외, 전체·위험·주의의 집계 기준과 직접 라벨 문구가 요청과 일치한다.

### Interaction and data checks

- 당월처럼 우측 여백과 세로 간격이 충분하면 기존 직접 라벨을 유지한다.
- 전월처럼 우측 끝 또는 라벨 간격이 좁으면 자동 콜아웃으로 전환한다.
- 해외 선택: 전체 7건 / 위험 4건 / 주의 0건.
- 미선택: 전체 7건 / 위험 0건 / 주의 0건.
- 국내 선택: 전체 7건 / 위험 3건 / 주의 0건.
- APS 부족수량 카드의 안전재고 필터는 별도 기능으로 유지된다.
- 컴파일, 실제 Windows 렌더링, 앱 응답 상태를 통과했다.

### Remaining findings

- 실행 가능한 P0/P1/P2 차이는 남아 있지 않다.

## 생산실적 종합수율 곱·끝 라벨 글씨 통일 iteration

- source visual truth:
  - `C:\Users\심민식\AppData\Local\Temp\codex-clipboard-f345dfd6-55cd-4460-beb5-cd06a7b98ecc.png` (460 × 228)
  - `C:\Users\심민식\AppData\Local\Temp\codex-clipboard-879ba594-19bc-4e09-a0c8-be7d07f727a2.png` (166 × 132)
  - `C:\Users\심민식\AppData\Local\Temp\codex-clipboard-9d7fc46d-5a29-4570-9666-8dfeb29e0996.png` (835 × 196)
- implementation screenshot: `C:\Users\심민식\Documents\ChatGPT\관별 신규 프로그램(aps)\screenshots\생산실적_종합수율곱_라벨크기통일.png` (1936 × 1048)
- same-input comparison: `C:\Users\심민식\Documents\ChatGPT\관별 신규 프로그램(aps)\screenshots\qa_생산실적_종합수율곱_라벨크기통일_비교.png` (1700 × 1000)
- viewport: Windows 최대화 화면 1936 × 1048, device scale factor 1.
- state: 대시보드 당월(2026-08), 해외 선택, 수주 상세 닫힘.

### Findings and fixes

1. [P1] 생산실적 표의 Clear·Color `종합`이 각 공정 수율의 산술평균으로 계산되어 연속 공정 종합수율의 의미와 달랐다.
   - Fix: Clear 행과 Color 행을 완전히 분리해 각각 `사출 × 분리 × 하이드레이션 × 검사접착 × 누수규격`으로 계산했다.
   - Post-fix evidence: 실제 데이터 기준 Clear와 Color가 각각 독립 계산된 뒤 모두 반올림 `75.9%`로 표시된다.
2. [P2] 일자별 생산현황의 마지막 생산실적 라벨이 수율 라벨보다 작아 보였다.
   - Fix: 두 라벨을 Malgun Gothic 8pt bold로 통일하고 생산실적 텍스트 영역을 136 × 18px로 확장했다.
   - Post-fix evidence: 마지막 막대와 수율 선 끝의 직접 라벨이 동일한 크기·굵기로 표시되고 잘림이나 겹침이 없다.

### Required fidelity surfaces

- Fonts/typography: 직접 라벨 두 종류를 동일한 8pt bold로 통일했다.
- Spacing/layout rhythm: 생산실적 라벨 폭을 넓혀 `생산실적 - 39k pcs`가 한 줄로 안정적으로 표시된다.
- Colors/tokens: 생산실적 파랑, 종합수율 주황의 기존 의미 색상을 유지했다.
- Image quality/assets: 이미지 자산 변경 없음.
- Copy/content: 생산실적·수율 문구와 단위는 기존 업무 표현을 유지했다.

### Interaction and data checks

- 오프스크린 위젯 검사에서 Clear 종합 `75.9%`, Color 종합 `75.9%`를 확인했다.
- 각 행은 별도의 공정 수율 배열을 곱하므로 동일한 표시값은 소수 첫째 자리 반올림 결과일 뿐 계산을 공유하지 않는다.
- 컴파일 성공, 실제 Windows 앱 응답 정상, 최대화 화면 캡처 정상.

### Remaining findings

- 실행 가능한 P0/P1/P2 차이는 남아 있지 않다.

## Total 월 종합수율·확인 실적·수주 상세 스크롤 iteration

- source visual truth:
  - `C:\Users\심민식\AppData\Local\Temp\codex-clipboard-20afe01d-63e0-494b-87e3-b68bddcff1a3.png` (792 × 135)
  - `C:\Users\심민식\AppData\Local\Temp\codex-clipboard-4f6a8004-0eb4-437e-aaeb-569cf38d9352.png` (801 × 135)
- implementation screenshots:
  - `C:\Users\심민식\Documents\ChatGPT\관별 신규 프로그램(aps)\screenshots\수주상세_상단고정_전체본문스크롤.png` (1936 × 1048)
  - `C:\Users\심민식\Documents\ChatGPT\관별 신규 프로그램(aps)\screenshots\수주상세_카드전환_맨위초기화.png` (1936 × 1048)
- same-input comparison: `C:\Users\심민식\Documents\ChatGPT\관별 신규 프로그램(aps)\screenshots\qa_Total월종합수율_수주상세스크롤_비교.png` (1500 × 1120)
- viewport: Windows 최대화 화면 1936 × 1048, device scale factor 1.
- state: 대시보드 당월(2026-08), 해외 선택, 수주 상세 열림.

### Findings and fixes

1. [P1] 생산실적 표에 Clear·Color만 있어 월 전체 수율을 직접 확인할 수 없었다.
   - Fix: Color 아래에 `Total` 행을 추가하고 월 전체의 공정별 수율과 다섯 공정 연속곱 종합수율을 표시했다.
   - Post-fix evidence: 당월 Total은 96.6% / 84.6% / 99.4% / 95.2% / 99.4%, 종합 76.8%이며 전월 Total 종합은 78.6%다.
2. [P1] 저장 상태 생산실적이 월 실적·수율 집계에 포함됐다.
   - Fix: 생산실적 집계 쿼리를 `stts='C'`인 접수·확인 처리 데이터만 사용하도록 제한했다. 원본 SQLite 보관은 유지하되 화면 집계에서는 `S(저장)`을 제외했다.
   - Post-fix evidence: 당월 화면 양품실적 11,653,854 pcs가 DB의 C 상태 합계와 정확히 일치하며 S 상태 243,397 pcs는 제외됐다.
3. [P2] 수주 상세의 기본정보 카드가 고정돼 제품 목록에 사용할 수 있는 세로 공간이 작았다.
   - Fix: 제목·수주번호·파란 요약줄만 고정하고 납기일 기본정보부터 공정별 부족수량 합계까지 하나의 세로 스크롤 본문으로 묶었다.
   - Post-fix evidence: 스크롤바가 파란 요약줄 바로 아래에서 시작하고 기본정보와 제품별 진행상태가 동일 영역 안에 표시된다.
4. [P2] 카드 전환 시 이전 스크롤 위치가 남을 가능성이 있었다.
   - Fix: 상세 조회 시작 시와 콘텐츠 재배치 직후 모두 세로 스크롤 값을 0으로 초기화했다.

### Required fidelity surfaces

- Fonts/typography: Total 행은 기존 표 글꼴을 유지하고 굵기로 월 전체 기준임을 구분했다.
- Spacing/layout rhythm: 고정 헤더 경계를 파란 요약줄 아래로 맞추고 그 아래를 연속 스크롤 영역으로 구성했다.
- Colors/tokens: Total은 옅은 회색 배경, 확인 실적 합계는 기존 파랑 계열을 유지했다.
- Image quality/assets: 이미지 자산 변경 없음.
- Copy/content: `Total`, 공정별 월 수율, 종합수율과 기존 수주 상세 문구를 유지했다.

### Interaction and data checks

- 당월·전월 전환 시 Clear / Color / Total 세 행이 함께 갱신된다.
- 당월 확인 실적 UI 합계 11,653,854 pcs = DB C 상태 합계 11,653,854 pcs.
- DB S 상태 양품실적 243,397 pcs는 모든 생산실적 화면 집계에서 제외된다.
- 수주 상세를 열 때 스크롤 위치를 즉시 0으로 초기화하고, 레이아웃 갱신 후 한 번 더 0으로 고정한다.
- 컴파일 성공, 실제 Windows 앱 PID 42860 응답 정상.

### Remaining findings

- 실행 가능한 P0/P1/P2 차이는 남아 있지 않다.

## 생산실적 셀 선택 대비·주의건 검증 iteration

- source visual truth:
  - `C:\Users\심민식\AppData\Local\Temp\codex-clipboard-94301770-a4ce-4ac5-bbe8-785a941a0f56.png` (811 × 442)
  - `C:\Users\심민식\AppData\Local\Temp\codex-clipboard-fd6eb67f-62d2-4155-be60-b63486686002.png` (496 × 129)
- implementation screenshot: `C:\Users\심민식\Documents\ChatGPT\관별 신규 프로그램(aps)\screenshots\생산실적_셀선택_고대비.png` (606 × 342)
- same-input comparison: `C:\Users\심민식\Documents\ChatGPT\관별 신규 프로그램(aps)\screenshots\qa_생산실적_셀선택_고대비_비교.png` (1250 × 500)
- viewport: Qt 표 위젯 606 × 342 집중 캡처, device scale factor 1.
- state: 대시보드 당월, 생산실적 Total 행 숫자 셀 선택.

### Findings and fixes

1. [P1] 청록색 선택 배경 위에 기본 진회색 글씨가 유지돼 선택값 판독성이 낮았다.
   - Fix: DashboardTable의 선택 글자색을 흰색으로, 선택 배경을 `#1F7784`로 명시했다.
   - Post-fix evidence: Total 숫자 셀 선택 시 흰색 굵은 글씨가 청록 배경과 충분한 대비로 표시된다.

### Required fidelity surfaces

- Fonts/typography: Total 행의 굵은 글꼴을 유지한 채 선택 시 흰색으로 전환된다.
- Spacing/layout rhythm: 셀 크기·패딩·표 구조 변경 없음.
- Colors/tokens: 선택 상태를 청록 `#1F7784`와 흰색 전경으로 통일했다.
- Image quality/assets: 이미지 자산 변경 없음.
- Copy/content: 표의 수율·실적 문구와 값 변경 없음.

### Interaction and data checks

- 강제 선택 상태 렌더링에서 Total 숫자 셀이 흰색 글씨로 표시되는 것을 확인했다.
- 리스크 원천 검증: 현재 D-4~D-7에는 8월 24일 한 건이 있으나 동일 수주 `R202608100002`에 D-1 품목이 있어 수주 단위 최조 납기 규칙에 따라 위험으로 합쳐진다.
- 8월 26일 이후 건은 D-8 이상으로 현재 주의 범위 밖이다. 수주당 한 카드 기준에서 주의 0건은 정상이다.
- 실제 Windows 앱 PID 47280 응답 정상.

### Remaining findings

- 실행 가능한 P0/P1/P2 차이는 남아 있지 않다.

final result: passed

## 공정별 Excel 내보내기 iteration

- 대상: 사출·분리·하이드레이션·검사·접착·누수·규격 전용 탭.
- 저장 규칙: 바탕화면 `YYMMDD_공정이름.xlsx`, `간략히보기`와 `납기별 상세` 두 시트.
- 필터 경계: 시장·통합검색·납기·분류·결과 내 검색·작업 가능만은 반영하고, 코드표시·품명기준·간략히보기 표시 상태는 내보내기 데이터에 영향을 주지 않는다.
- 공정 기준: 사출 R, 분리 Q, 이후 공정 P 기준으로 품명과 간략 묶음을 고정한다.
- 상세 코드: 전용 코드는 본문에 표시하고 나머지 코드는 오른쪽 끝에 기록한 뒤 실제 Excel 숨김 열로 저장한다.

### Functional QA

- 실제 APS 4,096행 기준 사출 상세 3,590행, R코드 간략 묶음 3,238행 생성.
- 표시 코드 전체 선택·판매명 전환·간략히보기 체크 전후의 내보내기 상세/간략 데이터 동일성 확인.
- 시트명·헤더·날짜/수량 서식과 Q·P·T 3개 숨김 열 확인.
- 수식 오류 `#REF!`, `#DIV/0!`, `#VALUE!`, `#NAME?`, `#N/A` 0건.
- 두 시트 렌더링 시 제목, 헤더, 본문 가독성 확인.
- `python -m py_compile ui/process_overview_page.py services/process_excel_exporter.py` 통과.

final result: passed

## 2026-08-19 최근 3개월 BOM 등록 복원 및 KPI 이동 QA

- 제품명 API의 등록일 원본 필드 `cdt`를 로컬 `in_dt`로 정규화했다.
- 전체 BOM 수집 완료: 제품 6,720건, 관계 28,399건, 약 16초.
- 최근 90일 신규 T코드 96건 확인: 2026-05-21 ~ 2026-08-07.
- 등록·수정 현황의 신규등록 목록은 현재 마스터 등록일과 실제 이력 이벤트를 중복 제거하여 합친다.
- 과거 수정 스냅샷이 없는 기간의 수정이력은 추측 생성하지 않는다. 현재 실제 수정 이벤트 0건.
- 각 공정 전용 화면의 `진행 대상 수주` KPI 카드는 공정 현황 전체 화면으로 이동한다.
- 자동 검증: `injection` 화면에서 카드 클릭 후 현재 페이지가 `process_overview`로 변경됨.

## 2026-08-19 APS 규격 파싱 및 분류 다중 버튼 QA

- Obsidian 기준 확인: 멀티 `PW / ADD`, 토릭 `PW / CP / AXIS`; POWER 뒤 규격 꼬리를 품목코드에서 분리한다.
- 멀티포컬 검증: 101/101행 ADD 추출 완료. 예: `R1121-05.25+1.00` → POWER `-05.25`, ADD `+1.00`.
- 토릭 검증: 2,842/2,842행 CP·AXIS 추출 완료. 판매코드에 AXIS가 생략된 경우 실제 P/Q/R 공정코드로 보완한다.
- 분류 드롭다운을 APS 기반 버튼으로 교체했다. 현재 공정 부족수량을 각 버튼에 표시한다.
- 분류 버튼은 복수 선택 OR이며 아무 선택도 없으면 전체로 복귀한다.
- 버튼 순서는 SCM/Obsidian 업무 순서 `FRP → 1-Day`, `Clear → Color`, `일반 → Silicone`, `Sph → M/F → Toric → Fix`를 사용한다.
- 1,900×1,000 오프스크린 렌더 확인: `screenshots/process_classification_buttons_add.png`.

## 2026-08-19 공정별 간략히 보기 코드 기준 QA

- 코드 체크박스는 열 표시만 제어하고 묶음 기준에는 관여하지 않는다.
- 사출 간략히 보기: 항상 R코드 기준.
- 분리 간략히 보기: 항상 Q코드 기준.
- 사출에서 Q·R 열을 동시에 표시한 검증 결과: 간략 결과 내 중복 R코드 0건.
- 도움말에 현재 공정과 고정 묶음 코드를 명시했다.

## 2026-08-19 진행 대상 수주 KPI·필터 초기화 QA

- 진행 대상 수주 KPI를 상단 시장/통합검색뿐 아니라 납기·분류·상세검색·작업가능·간략보기 결과에도 연동했다.
- 간략히 보기 묶음 행은 `_수주목록`의 실제 수주번호를 중복 제거하여 건수를 계산한다.
- 분리 탭 검증: 전체 25건 → `1-Day_Sph` 6건 → 간략히 보기 6건 → 초기화 25건.
- 각 공정 탭의 필터 초기화를 파란색 `PrimaryButton`으로 변경하고 흰색 초기화 아이콘을 적용했다.

## 2026-08-19 필터 초기화 상단 배치 보정

- 필터 초기화를 하단 상세 필터 줄에서 제거하고 상단 `조회` 버튼 바로 옆으로 이동했다.
- 초기화는 흰색 `SecondaryButton`으로 복원해 조회 주 동작과 시각적 위계를 구분했다.
- `완료 · S관(3공장) N건` 상태 문구는 화면에서 제거했다.
- 새 버튼으로 상단 검색·시장조건·납기·분류·코드표시·품명기준을 모두 초기화하는 동작을 검증했다.

## API 상태·자동 데이터 정리 iteration

- BOM 원천이 기존과 동일해 DB 교체를 생략한 `skipped` 상태를 정상·최신으로 처리한다.
- 대시보드 API 종합 상태는 success와 skipped를 모두 정상으로 인정한다.
- 설정 상세의 BOM 상태는 `정상 · 변경 없음`으로 표시한다.
- 수동 `불필요 데이터 정리` 버튼을 제거했다.
- 자동 정리는 프로그램 실행 20초 후 수행하고 이후 6시간마다 반복한다.
- 수집 중이면 자동 정리를 1분 뒤 재시도한다.
- BOM API는 갱신 메타가 비어 있어 동일 건수의 내용 변경을 메타만으로 감지할 수 없으므로, 전체 스냅샷 해시 비교와 변경 시 DB 교체 방식을 유지한다.

### Functional QA

- 현재 BOM skipped 상태에서 대시보드 `API 전체 양호`, state=ready 확인.
- 설정 BOM `정상 · 변경 없음`, state=success 확인.
- 수동 정리 버튼 미생성 확인.
- 자동 정리 타이머 활성 및 21,600,000ms(6시간) 확인.
- `python -m py_compile ui/main_window.py` 통과.

final result: passed

## 공정현황 KPI와 전용 탭 총량 일치 iteration

- 공정현황 메인의 기본 공정 필터가 누수규격으로 선택되어 각 KPI가 누수규격 조건부 합계로 계산되던 원인을 수정했다.
- 공정현황 메인의 최초/초기화 공정 기준을 `전체`로 변경했다.
- 사용자가 특정 공정을 직접 선택한 경우에만 KPI가 해당 공정 행 범위의 조건부 합계로 좁혀진다.

### Functional QA

- 전체 기준 공정현황 사출 KPI: `11,471,653 pcs`.
- 사출 전용 탭 사출 KPI: `11,471,653 pcs`로 일치.
- 누수규격 직접 선택 시 조건부 사출 KPI: `9,674,285 pcs`.
- 필터 초기화 후 전체 및 `11,471,653 pcs` 복귀 확인.
- `python -m py_compile ui/process_overview_page.py ui/main_window.py` 통과.

final result: passed

## 작업 가능만 · 간략히 보기 iteration

- 다섯 공정 탭의 필터 배치는 공통 컴포넌트로 유지한다.
- 사출: `간략히 보기`를 제공하고 R코드가 같은 수주를 한 행으로 합친다.
- 분리: `작업 가능만`과 `간략히 보기`를 모두 제공한다.
- 하이드레이션·검사/접착·누수/규격: 리드지 이후 작업 조건 차이를 고려해 `간략히 보기`를 숨기고 `작업 가능만`만 제공한다.
- 작업 가능은 현재 공정 부족이 있으면서 직전 공정 부족이 0인 행으로 정의한다.
- 간략히 보기의 대표 납기는 묶인 수주 중 최솟값이며 이 날짜를 기준으로 오름차순 정렬한다.
- 수주번호는 `N건 묶음`, tooltip은 포함 수주번호 전체 목록을 표시한다.
- 코드가 없는 행은 서로 합치지 않는다.

### Functional QA

- 사출 상세 3,590행 → R코드 기준 3,238개 묶음.
- 사출 부족수량 합계는 묶기 전후 `11,471,653 pcs`로 동일.
- 분리 `작업 가능만` 결과의 모든 행에서 사출 부족수량 0 확인.
- 분리 간략 보기 결과가 가장 빠른 납기일 오름차순임을 확인.
- 하이드레이션에서 간략히 보기 숨김, 작업 가능만 표시 확인.
- `python -m py_compile ui/process_overview_page.py ui/main_window.py` 통과.

final result: passed

## 현재 테이블 구성 연동 마스터 검색 iteration

- 마스터 검색이 숨겨진 P/Q/R 품명 전체를 검색하던 동작을 제거했다.
- 검색 범위는 공통 식별 필드, 현재 체크된 코드 열, 현재 선택된 품명 기준, POWER/CP/AXIS/ADD로 구성한다.
- 품명 기준이나 코드 표시 체크를 변경하면 활성 검색어를 새 범위로 즉시 재평가한다.
- 표 내부 가시성 동기화에서는 검색 재평가 신호를 내지 않아 재귀 조회를 방지한다.

### Functional QA

- 검사·접착 탭 `판매명 + SOUL`: 표시 판매명에 SOUL이 있는 21행만 반환.
- 이전 오탐 `PIA_KEOPI BROWN`: 0행 확인.
- 반환된 모든 행이 현재 검색 범위 중 최소 한 필드에서 SOUL 부분일치함을 확인했다.
- `python -m py_compile ui/process_overview_page.py ui/main_window.py` 통과.

final result: passed

## 마스터 통합검색 · 현재 공정 카드 강조 iteration

- 상단 통합검색 대상을 신규분류·이니셜·수주번호·T/P/Q/R 품번·각 공정 품명·POWER/CP/AXIS/ADD로 제한했다.
- 쉼표 `,` 및 전각 쉼표 `，`를 OR 조건으로 처리하고 중복·빈 검색어를 제거한다.
- `*` 또는 빈 검색은 전체 조회로 처리한다.
- 하단 검색은 `현재 결과 내` 검색임을 placeholder에 명시했다.
- 공정 전용 탭에서 현재 공정 KPI 카드만 옅은 파란 배경과 2px 파란 테두리로 강조한다.
- 비선택 KPI 카드는 일반 흰색/회색 테두리이며 hover 때만 파란색을 사용한다.

### Functional QA

- `AA1519,Q1113` 결과가 두 단일 검색 결과의 합집합과 일치함을 확인했다.
- `*` 결과와 빈 검색 결과가 각각 4,096행으로 동일함을 확인했다.
- 분리 탭에서 분리 KPI만 `selectedProcess=true`, 나머지 네 카드는 false임을 확인했다.
- `python -m py_compile ui/process_overview_page.py ui/main_window.py` 통과.

final result: passed

## 공정별 APS 코드·품명 기준 iteration

- 사출: R코드만 기본 표시, 품명 기준 `사출명(품명R)`.
- 분리: Q코드만 기본 표시, 품명 기준 `분리명(품명Q)`.
- 하이드레이션·검사/접착·누수/규격: P코드만 기본 표시, 품명 기준 `생산명(품명P)`.
- 다섯 공정 메뉴 모두 세부 진행 현황 UI를 공유하는 고정 공정 페이지로 전환했다.
- 각 탭은 해당 공정 부족수량이 0보다 큰 APS 행만 표시한다.
- 최초 APS 조회 결과를 모든 공정 탭이 공유하고, APS 갱신 때 한 번만 DB를 읽어 각 화면에 전달하도록 구성했다.
- BOM 제품명 검색과 BOM 관계 데이터는 사용하지 않는다.

### Functional QA

- injection: 사출 / R코드 / 사출명 일치.
- separation: 분리 / Q코드 / 분리명 일치.
- hydration: 하이드레이션 / P코드 / 생산명 일치.
- inspection: 접착 / P코드 / 생산명 일치.
- leak: 누수규격 / P코드 / 생산명 일치.
- 다섯 탭의 표시 행 전체에서 해당 공정 수량 `> 0` 확인.
- `python -m py_compile ui/process_overview_page.py ui/main_window.py` 통과.

final result: passed

## 사출 전용 공정 현황 · APS 아웃바운드 코드 관계 iteration

- `납기 통합조회` 메뉴와 빈 페이지를 제거했다.
- 사출 메뉴는 `세부 진행 현황` UI와 기능을 공유하는 `ProcessOverviewPage(fixed_process="사출")`로 구성했다.
- 표 데이터는 S관 APS 스냅샷 `aps_plan`만 사용하며 BOM 제품명 등록검색/BOM 관계 조회를 추가하지 않는다.
- T코드는 APS `demand_item_id`, P/Q/R은 같은 수주·이니셜·제품·POWER 묶음의 APS `item_id` 접두 코드다.
- 사출 전용 화면에서 T/P/Q/R을 `APS 코드 관계`로 기본 펼침 표시하고 사용자가 각 코드를 개별로 숨길 수 있게 했다.
- 필터 후 표시되는 모든 행의 사출 필요수량이 0보다 큰지 검증했다.
- APS DB 변경 감시 시 세부 진행 현황과 사출 전용 화면을 함께 갱신한다.

### Functional QA

- 원천 4,096행 중 사출 조건 표시 상한 3,000행 검증.
- 샘플 `Iris SoulBrown_40팩 / -01.25`에서 APS 코드 `S129 / P0010A / Q0010 / R0010` 관계 확인.
- 납기 통합조회 nav/page 미등록 확인.
- `python -m py_compile ui/process_overview_page.py ui/main_window.py` 통과.

final result: passed

## 공정현황 6개 KPI 카드 iteration

- KPI를 `진행 대상 수주 / 사출 / 분리 / 하이드레이션 / 검사·접착 / 누수·규격` 6개로 재구성했다.
- 첫 카드는 기존 의미를 유지해 상단 시장·통합검색 기준 수주 건수를 표시한다.
- 5개 공정 카드는 상단 조건과 하단 납기·분류·공정·상세검색 조건을 모두 반영한 부족수량 합계를 축약 없는 `#,##0 pcs`로 표시한다.
- 기본 조건 실제값: 사출 9,674,285 / 분리 9,159,487 / 하이드레이션 8,450,802 / 검사·접착 9,211,047 / 누수·규격 7,781,706 pcs.
- +7일 필터 변경 직후 5개 카드 수량이 각각 재계산되는 것을 확인했다.
- 공정 카드 클릭 경로 검증: injection / separation / hydration / inspection / leak.
- 클릭 가능 카드는 파란 테두리·hover 배경·우측 화살표로 구분한다.
- evidence: `screenshots\process_six_kpis_full_pcs.png`

final result: passed

## 공정현황 필터 초기화 · 검색어 즉시 해제 iteration

- 하단 필터 카드 우측에 아이콘 포함 `필터 초기화` 버튼을 추가했다.
- 초기화 범위: 상단 통합검색, 시장, 납기, 분류, 상세검색, 공정 표시, 코드 표시, 품명 기준.
- 통합검색의 X 또는 Backspace로 검색어가 빈 문자열이 되는 즉시 전체 데이터로 복귀하며 조회 버튼을 다시 누르지 않는다.
- 검증: 수주번호 검색 2행 → 검색어 clear 직후 화면 상한 3,000행으로 즉시 복귀.
- 초기화 후에도 직접 납기 등 모든 필터 조작이 유지된다.
- evidence: `screenshots\process_filter_reset_and_live_clear.png`

final result: passed

## 공정현황 품명 가독성 iteration

- 신규분류요약 폭을 210px → 150px, 수주번호 폭을 122px → 100px로 축소했다.
- 단일 stretch 열인 품명은 동일 1920px 화면에서 210px 수준 → 실제 293px로 확대됨을 확인했다.
- 표는 한 줄 말줄임으로 고정해 행 높이 안에서 텍스트가 겹치지 않는다.
- 신규분류요약·수주번호·품명·T/P/Q/R코드는 마우스 hover 시 전체 값을 툴팁으로 제공한다.
- evidence: `screenshots\process_product_name_width_rebalanced.png`

final result: passed

## 리스크 우클릭 메뉴 polish iteration

- Windows 기본 컨텍스트 메뉴 대신 360px 흰 카드형 메뉴를 적용했다.
- 상단에 이니셜·수주번호를 별도 헤더로 표시하고 작업 영역과 구분선으로 분리했다.
- 메뉴 행은 24px 이상 클릭 영역, 8px 반경, 파란 아이콘을 사용한다.
- hover/선택 상태는 `#E8F2FF` 배경과 `#075CCF` 글씨로 컨트롤타워 필터와 통일했다.
- evidence: `screenshots\risk_context_menu_polished.png`

final result: passed

## 리스크 우클릭 연계 · 범례/필터 대비 iteration

- 차트 범례를 `생산실적`, `금일 생산실적(진행중)`, `종합수율`로 통일했다.
- 초록 막대 옆 라벨을 `금일 실적`으로 변경했다.
- 리스크 카드 좌클릭은 기존 수주 상세를 유지한다. 우클릭은 `수주 상세 보기`와 `공정현황에서 수주번호 검색` 메뉴를 먼저 표시하고 선택한 작업만 실행한다.
- 우클릭 연계 시 공정현황 최초 진입 기본값(전체 시장·납기 해제·분류 전체·누수규격·코드 숨김·판매코드)으로 초기화하고 해당 수주번호를 상단 통합검색에 입력한다. PB·이니셜 없음도 같은 규칙이다.
- 연계 조회 후 직접 납기, 전체 공정, T코드 표시, P코드 품명 기준으로 각각 변경되는 것을 검증해 필터 조작이 잠기지 않음을 확인했다.
- 실제 PB 리스크 `R202607290003` 공정현황 연계 검증: 공정현황 전환, 검색어 R202607290003, 결과 9행.
- 컨트롤타워 실제 QSS를 기준으로 필터의 높이 34px, 좌우 패딩 14px, 반경 9px, 기본 테두리 `#D7DCE3`, 선택 배경 `#E8F2FF`, 선택 글씨·테두리 `#0A7AFF`를 동일 적용했다.
- evidence: `screenshots\risk_rightclick_process_search_filters_reset.png`, `screenshots\control_tower_exact_filter_selected.png`

final result: passed

## 생산실적 07시 갱신 · 다중 PC · 선택 대비 iteration

- 07시 이후 당일 첫 실행: 전월 1일~금일 전체 재수집 후 원자적 DB 교체.
- 같은 날 후속 실행: 최근 7일만 재수집하며 해당 날짜 범위를 삭제 후 키 기준 재삽입.
- 실행 검증: 2026-08-19 첫 전체 21,594건, 후속 최근 7일 3,184건, 중복 0건.
- 확정 기준: 18일까지 파란 막대와 주황 수율, 19일은 초록 진행 막대이며 수율 집계에서 제외.
- 차트 라벨: `수율 71.2% · 실적 153k pcs`를 주황색 한 줄로 표시하고 `오늘 실적 15k pcs`를 초록 막대 옆에 표시. 세로 점선 제거.
- 자동 갱신: 실행 직후 보정 수집, APS 1분 변경 확인, 생산실적 1시간 확인, DB 변경 시 가공 화면 즉시 새로고침.
- 보존 정리: 최신 원천 스냅샷과 DB만 유지. 실제 실행에서 불필요 파일 17개(328,793,508 bytes) 제거.
- 선택 대비: Windows 네이티브 팔레트까지 고정해 선택 행을 `#E8F2FF` 배경과 `#075CCF` 글씨로 표시. 필터·콤보 팝업도 동일 토큰 적용.
- evidence: `screenshots\process_selection_control_tower_contrast_v2.png`, `screenshots\production_confirmed_and_today_adjacent.png`

final result: passed

## 당일 진행 중 초록 막대 표시

- 당월 일자별 생산현황에서 오늘 생산량을 초록 막대와 초록 점선으로 표시한다.
- 초록 막대 라벨은 `19일 생산실적 · 13k pcs`처럼 현재 진행 수량을 표시한다.
- 카드 상단에도 `오늘 19일 · 진행 중` 초록 상태 칩을 추가했다.
- 오늘 진행 막대는 확정 실적 합계와 수율 계산에 포함하지 않는다. 파란 막대와 주황 수율선은 전일 확정분 기준이다.
- 전월 선택 시 오늘 진행 중 칩과 차트 마커는 자동으로 숨긴다.
- 당월→전월→당월 전환에서 표시 상태를 검증했다.

final result: passed

## 설정 및 운영 · 데이터 수집 주기/상태 패널

- `데이터 수집 및 갱신` 카드의 `상세 설정 펼치기/접기`로 운영 패널을 토글한다.
- 데이터별로 현재 상태, 마지막 갱신일시, 저장 건수, 자동 주기, `지금 갱신` 버튼을 표시한다.
- 기본값: BOM 수동만, S관 APS 원천 변경 확인 1분, 생산실적 수동만.
- BOM·생산실적은 수동/30분/1·3·6·12·24시간, APS는 중지/1·5·10·30·60분 중 선택한다.
- 변경값은 `C:\똑딱이 생산3팀 API DATA\settings\collection_schedule.json`에 즉시 저장되고 재시작 후 복원된다.
- 각 수집은 별도 QProcess로 실행하며 중복 수집을 막고, 전체/BOM/APS/생산실적 수동 실행을 구분한다.
- 자동 수집은 프로그램 실행 중에만 동작한다는 안내를 패널에 표시한다.

## ERP 07시 마감 · 확정/진행 데이터 분리

- 수집기는 전일 확정분과 당일 진행량을 함께 저장한다. 2026-08-19 실행 기준 범위는 2026-07-01~2026-08-19이다.
- 대시보드는 전일까지의 `C` 상태만 확정 실적·수율로 집계하고, 당일 `C/S` 누수·규격 양품수량은 별도 진행 막대로만 사용한다.
- 2026-08-19 실제 진행량 13,110 pcs는 초록 막대로 표시되며 확정 당일값은 0, 당일 종합수율은 미계산 상태임을 검증했다.
- 일자별 생산현황 배지를 `2026-08 · 08-18 확정`으로 표시해 마감 기준을 명확히 했다.
- 월 1일에는 전월 말일까지 확정 집계하고 기존 전월 기본 선택 규칙을 유지한다.

final result: passed

## 세부 진행 현황 · 컨트롤타워형 가상 표 및 P/Q/R 품명 연결

- 확인 결과 GUI 실행 중 BOM 수집 프로세스는 없었다. BOM 갱신은 설정의 명시적 전체 수집에서만 시작된다.
- 기존 병목은 필터마다 최대 3,000행의 모든 셀 객체를 다시 생성하는 `QTableWidget` 렌더링이었다.
- 컨트롤타워의 조회·필터·정렬 결과는 유지하고 `QAbstractTableModel` 기반 가상 표로 교체해 보이는 셀만 렌더링한다.
- S관 APS의 `item_id → item_name`을 연결해 P/Q/R 코드 선택 시 실제 단계별 품명이 표시된다.
- P/Q/R 품명 보유 행: P 3,714 / Q 3,234 / R 3,590행.
- 전환 성능: 품명 0.0004~0.0008초, 전체 공정 0.0185초, +7일 0.0537초, 페이지 이동 0.002초 이하.
- 4,096행 데이터, 선택·스크롤·열 숨김·필터·툴팁·원본 표 스타일을 검증했다.

final result: passed

## 세부 진행 현황 · 품명 기준 즉시 전환 iteration

- 증상: 판매/P/Q/R 품명 기준 변경 시 상위 3,000행의 전체 셀을 재생성해 GUI가 약 0.4초씩 멈췄다.
- 수정: 현재 필터·정렬 결과를 유지하고 보이는 `품명` 열 1개만 제자리에서 갱신하도록 분리했다.
- 성능: 3,000행 기준 판매/P/Q/R 전환이 각각 0.0045~0.0059초로 단축됐다.
- 정합성: 각 선택에서 `품명판매`, `품명P`, `품명Q`, `품명R` 원천값과 화면 셀 값이 일치한다.
- 기존 납기·분류·공정·검색·정렬·선택 상태는 재조회 없이 유지된다.

final result: passed

## 공정 현황 · 컨트롤타워 세부 진행 현황 복제

- source component: `C:\Users\심민식\Documents\이상호\개인프로그램\SCM Control Tower ver.2\modules\aps-order-progress\ui\main_window.py`의 `DueDetailPage`
- source screenshot: `screenshots\control_tower_process_detail_reference.png` (1440 × 900, Qt native offscreen)
- implementation screenshot: `screenshots\process_detail_control_tower_s_fixed.png` (1440 × 900, Qt native offscreen)
- S관 데이터: `C:\똑딱이 생산3팀 API DATA\process-status\aps_process_status.sqlite`

### Fidelity ledger

1. 상단 진행현황 필터, KPI 4개, 납기·분류·검색 필터, 공정·코드·품명 표시 제어, 전체 폭 상세표의 원본 순서와 3단 화면 구조를 유지했다.
2. 납기 필터 `해제/직접/당월/+7일/+14일`, 진행현황 `전체/해외/PB/국내/안전`, 공정 보기 `전체/사출/분리/하이드레이션/접착/누수규격/포장`을 동일하게 구현했다.
3. T/P/Q/R 코드 열 표시, 판매/P/Q/R 품명 기준, 분류 및 통합검색, 원본 정렬 규칙과 최대 3,000행 표시를 유지했다.
4. 카드 반경, 필터 선택색, KPI 색상, 상태 칩, 표 헤더·행 높이는 원본 QSS 토큰을 S관 앱 스타일에 연결했다.
5. 의도적 차이는 관별 선택을 제거하고 `S관(3공장)` 고정 칩으로 표시한 것뿐이며, 조회·KPI·표는 생산3팀 로컬 SQLite만 사용한다.

### Functional QA

- S관 원천 4,096행 로드 성공.
- +7일 납기 필터 결과 423행 표시.
- 시장 5개, 납기 5개, 공정 7개, 코드 토글 4개, 품명 기준 4개 구성 확인.
- 해외+국내 복수 선택, 안전 단독 선택, 통합검색 후 복구를 확인했다.
- P코드 숨김→표시와 누수규격→전체 공정 열 확장을 확인했다.
- 15초 DB 파일 변경 감지 시 같은 화면이 자동 갱신된다.
- Python 컴파일과 1440 × 900 렌더에서 오류가 없다.

### Findings

- 실행 가능한 P0/P1/P2 차이는 남아 있지 않다.
- Qt offscreen 캡처에서는 Windows 한글 글리프가 사각형/공백으로 보이지만 실제 Windows GUI에서는 기존 Malgun Gothic 렌더링이 정상 적용된다.

final result: passed

## BOM 현황 전체 페이지 · SCM Control Tower 원본 복제

- source component: `C:\Users\심민식\Documents\이상호\개인프로그램\SCM Control Tower ver.2\ui\bom_page.py`
- mounted component: `ui\bom_page.py` (`BomStatusPage`, 원본 대비 코드 변경 없음; 파일 끝 개행만 추가)
- source service: 원본 `services\bom_explorer.py`와 동일, 데이터 경로만 생산3팀 전용으로 변경
- S관 BOM DB: `C:\똑딱이 생산3팀 API DATA\bom\product_reference.sqlite`
- S관 APS DB: `C:\똑딱이 생산3팀 API DATA\aps\aps_yield.db`
- S관 품목코드 캐시: `C:\똑딱이 생산3팀 API DATA\item-codes\item_codes.sqlite`
- 1920 × 1009 screenshots: `bom_clone_tree_p1186.png`, `bom_clone_product.png`, `bom_clone_item_code.png`, `bom_clone_changes.png`

### Fidelity and function verification

1. 내부 탭 4개와 탭 순서가 원본과 일치한다.
2. 검색 모드·코드 범위·자동완성·초기화·5단계 트리·hover·우클릭·단계 복사 UI를 원본 클래스 그대로 사용한다.
3. 제품명 등록 검색의 9개 필터, 조합 드롭다운, 확장 구성, 더블클릭 BOM 이동을 원본 클래스 그대로 사용한다.
4. 품목코드 구성의 판매·생산 선택, 분리·사출 연결, API 코드 표, 헤더 포함 Excel 복사, 진행·취소 UI를 원본 클래스 그대로 사용한다.
5. BOM 등록·수정의 기간·공장·단계 필터와 신규/수정 표를 원본 클래스 그대로 사용한다.
6. 원본 BOM QSS 선택자를 함께 복제했으며 메인 앱은 `BomStatusPage`를 직접 마운트한다.
7. `P1186` 조회 결과 단계별 1·1·3·1·2개, 단계 복사 클립보드 일치 확인.
8. 제품 6,720건 로드, `1-Day` 조건에서 공장 후보 4→3개 축소 및 초기화 복구 확인.
9. Python 컴파일 및 4개 탭 오프스크린 렌더 통과.

final result: passed

## BOM 내부 4개 탭 · 필터/자동완성/연결조회 iteration

**비교 대상 및 실행 증거**

- source visual truth:
  - `C:\Users\심민식\AppData\Local\Temp\codex-clipboard-0e387081-538d-468d-95a6-9b1d020d3931.png`
  - `C:\Users\심민식\AppData\Local\Temp\codex-clipboard-8b42fccf-8f91-468d-901d-42efa3011610.png`
  - `C:\Users\심민식\AppData\Local\Temp\codex-clipboard-407bdc38-9e26-4af2-96c5-16c1b64b754a.png`
- implementation screenshots:
  - `screenshots\qa_bom_tabs_initial.png` — 구성현황 `1118` 자동완성 3건
  - `screenshots\qa_state_after_nav.png` — 제품명 등록 열별 필터
  - `screenshots\qa_bom_item_code_result.png` — 품목코드 직상위·직하위 결과
  - `screenshots\qa_bom_change_tab.png` — 등록·수정 현황 필터 레이아웃
- viewport: 1920 × 1009, Windows native PySide6, second monitor.

**Findings and fixes**

1. [P1] 구성현황 자동완성 모델이 타이핑 뒤 교체돼도 팝업을 다시 열지 않아 후보가 한 건처럼 보였다.
   - Fix: 모델 갱신 후 포커스와 결과가 있으면 `QCompleter.complete()`를 호출한다.
   - Result: `1118` 입력 시 `BC1118`, `P1118`, `T1118` 3건이 동시에 표시된다.
2. [P1] 제품명 등록 검색이 단일 입력창이라 기준 화면의 열별 필터와 달랐다.
   - Fix: 제품명코드·제품명·구분·공장·유효기간·DIA·BC·분류요약·함수율 필터 행, 500건 표시 제한, 결과 수, 초기화, 확장 구성을 구현했다.
   - `*1186`은 접두어를 무시해 `P1186`, `T1186`을 함께 찾고 `*Rhapsody`는 품명 마스터 문자 검색으로 동작한다.
3. [P2] 필터 위젯을 클릭할 때 표의 행 선택색이 필터 전체 뒤에 나타나 시인성이 떨어졌다.
   - Fix: 필터 표는 선택을 사용하지 않고 더블클릭 이동 신호만 유지했다.
4. [P1] 품목코드 탭에 검색 범위·자동완성·초기화·결과 이동이 없었다.
   - Fix: 통합/코드/품명과 T/S/P/Q/R 범위, 자동완성, 로컬 DB 전용 상태, 직상위·직하위 테이블, 더블클릭 BOM 이동을 구현했다.
5. [P2] 등록·수정 현황이 정적 표 두 개뿐이었다.
   - Fix: 신규등록 생산공장 필터, 수정현황 BOM 단계 필터, 필터 초기화, 최근 90일·비교 기준·결과 건수 상태를 추가했다.

**Interaction and data checks**

- 오프스크린 Qt 실행 검증: BOM 내부 탭 4개 생성, 제품 기준정보 6,720건 중 최대 500건 표시.
- `*1186` 제품 필터: `P1186`, `T1186` 2건 확인.
- `P1186` 품목 연결: 직상위 `T1186`, 직하위 `Q1113` 확인.
- `1118` 자동완성: `BC1118`, `P1118`, `T1118` 확인.
- 모든 탭은 별도 수집된 로컬 SQLite만 읽고 직접 API 조회는 하지 않는다.
- `python -m py_compile ui/main_window.py services/bom_explorer.py` 통과.
- 실제 Windows 앱 PID 39496 실행 및 응답 확인.

### Remaining findings

- 현재 로컬 비교 스냅샷에는 신규등록·수정 이벤트가 0건이므로 등록·수정 표가 비어 있는 상태가 정상이다. 필터와 상태 문구는 이벤트가 생기면 같은 로컬 이력 DB에서 즉시 갱신된다.
- 실행 가능한 P0/P1/P2 차이는 남아 있지 않다.

final result: passed

## BOM 구성 현황 · 컨트롤타워 필터/트리/상호작용 iteration

**비교 대상**

- source visual truth path: `C:\Users\심민식\AppData\Local\Temp\codex-clipboard-6acbd50b-c4e0-42de-9294-0e3eda9f1d95.png`
- implementation screenshot path: `screenshots\BOM_실행본_통합검색_트리.png`
- full-view comparison evidence: `screenshots\qa_BOM_컨트롤타워_필터트리_비교.png`
- focused hover evidence: `screenshots\BOM_카드_호버상세_실행본.png`
- focused context-menu evidence: `screenshots\BOM_카드_우클릭메뉴_실행본.png`
- source pixels: 1919 × 1050
- implementation pixels / viewport: 1920 × 1009, Windows native PySide6, 100% capture
- state: BOM 구성 현황, 통합 검색·전체 코드, `P1186` 조회 완료

**Comparison history**

1. Earlier implementation used one plain text field, five independent layout columns and arrow glyphs. It had no path highlight, stage copy, detailed hover or node context menu.
2. Replaced it with the control-tower interaction model: search-mode and code-scope filters, suggestions, graph edges, selected-path emphasis, per-stage copy, hover detail and right-click actions.
3. Post-fix full-view comparison confirms the same five-stage reading order, matching card/edge density and operational controls. Focused captures confirm hover details and all three context-menu actions.

**Required fidelity surfaces**

- Fonts and typography: existing Malgun Gothic application hierarchy retained; code labels are bold and names are elided consistently with the reference.
- Spacing and layout rhythm: five equal stage tracks, compact header/copy controls, full-width graph canvas and bottom state note align with the reference structure.
- Colors and visual tokens: blue selected state, stage-prefix accents, muted inactive paths and white/gray surfaces use the established application tokens.
- Image quality and asset fidelity: existing mascot and icon library assets retained; no placeholder, handmade SVG or text-symbol asset substitution was introduced.
- Copy and content: filter labels, stage titles, local-DB status, hover fields and right-click commands are Korean operational copy matching the reference behavior.

**Primary interactions tested**

- Local SQLite-only `P1186` search returned 8 graph nodes.
- Clicking `BS0314` highlighted only `T1186 → P1186 → BS0314` and dimmed unrelated branches.
- Stage copy put `BS0314` on the system clipboard and changed button feedback to `복사됨 ✓`.
- All 8 node cards contain detailed tooltips.
- Right-click shows `기준으로 재조회`, `품번 복사`, `품번·품명 복사`.
- Search reset and result-state updates were exercised by direct UI methods.

**Findings**

- No actionable P0/P1/P2 mismatch remains for the requested BOM configuration screen.
- P3: the production app intentionally retains the production-team sidebar instead of copying the SCM Control Tower navigation groups.

**Implementation Checklist**

- [x] Search field and code-scope filters
- [x] Local BOM SQLite-only reads

## 제품명 등록 검색 · 컨트롤타워 패싯 필터 복제 iteration

- reference: `C:\Users\심민식\AppData\Local\Temp\codex-clipboard-8521f548-dd4d-48f9-b0da-2e6f823d9658.png`
- SCM Control Tower source: `C:\Users\심민식\Documents\이상호\개인프로그램\SCM Control Tower ver.2\ui\bom_page.py`
- implementation screenshot: `screenshots\bom_product_filter_faceted.png` (1600 × 950, Qt offscreen)
- data source: S관 전용 `C:\똑딱이 생산3팀 API DATA\bom\product_reference.sqlite`; 컨트롤타워 DB 연결은 복사하지 않음.

### Fidelity ledger

1. 열 순서와 필터행: 제품명코드·제품명·구분·공장·유효기간·DIA·BC·분류요약·함수율 구조를 유지했다.
2. 조합 필터: 각 드롭다운은 자기 열을 제외한 나머지 활성 조건을 만족하는 행에서만 후보를 만든다.
3. 선택 안정성: 조합상 유효하지 않게 된 선택은 `전체`로 복구하고 후보를 한 번 더 계산한다.
4. 컨트롤 상태: 확장 구성 선택 표시는 원본과 같은 `확장 구성 ✓`, 도움말은 직접 상·하위 품번 표시로 맞췄다.
5. 드롭다운 표현: hover/focus와 팝업 선택색을 기존 파란색 디자인 토큰에 맞췄다.
6. 데이터 경계: UI·필터 동작만 복제하고 조회 서비스와 SQLite 경로는 생산3팀 전용 구성을 유지했다.

### Functional QA

- 로컬 제품 6,720건으로 검증.
- 제품명 `1-Day` 필터 시 430건, 공장 후보 4개 → 3개로 축소.
- 필터 초기화 시 공장 후보 4개로 복구.
- `python -m py_compile ui\main_window.py gui_app_pyside6.py` 통과.
- [x] Five-stage connected graph
- [x] Hover detail tooltip
- [x] Click path highlight
- [x] Right-click requery and copy actions
- [x] Stage-level active-code copy
- [x] Native Windows render verification

final result: passed
