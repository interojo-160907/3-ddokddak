# 똑딱이 생산3팀

생산3팀 납기 통합조회 프로그램의 PySide6 GUI 프로젝트입니다.

APS·BOM·생산실적 API를 로컬 SQLite 스냅샷으로 수집하고 화면에서 조회합니다.

대시보드의 기본 관은 `config.py`의 `DEFAULT_FACTORY = "S관"`으로 관리합니다. 생산3팀은 S관을 기본으로 사용하며, 추후 A관·C관 요청 시 같은 화면 구조를 재사용할 수 있습니다.

## 실행

```powershell
python gui_app_pyside6.py
```

## 현재 메뉴

- 대시보드
- 납기 통합조회
- 공정 현황
  - 사출
  - 분리
  - 하이드레이션
  - 검사·접착
  - 누수·규격
- 실시간 실적 반영
- BOM 현황
- 설정 및 운영

## 데이터 폴더

개발 단계의 기본 데이터 공간은 프로젝트의 `data` 폴더입니다.

- `data/api_cache`: 화면 조회용 API 임시 캐시
- `data/api_raw`: API 원문 보관
- `data/backup`: SQLite 업데이트 전 백업
- `data/snapshots`: APS 갱신 시점별 스냅샷

설치 버전에서는 인스톨러에서 사용자가 데이터 저장 위치를 선택할 수 있도록 연결할 예정입니다.

### 생산3팀 전용 API 저장소

SCM Control Tower의 중앙 DB와 공유하지 않고 다음 전용 저장소를 사용합니다.

```text
C:\똑딱이 생산3팀 API DATA\
└─ bom\
   ├─ product_reference.sqlite   # 화면이 즉시 조회하는 현재 스냅샷
   ├─ raw_api\                   # API 원문 gzip 압축본
   ├─ backup\                    # 갱신 전 SQLite 백업
   └─ snapshot\refresh_status.json
```

BOM 수집 실행:

```powershell
python collectors\bom_snapshot_collector.py
```

## 공정현황 및 생산실적 스냅샷

- 공정현황: `C:\똑딱이 생산3팀 API DATA\process-status\aps_process_status.sqlite`
- 생산실적: `C:\똑딱이 생산3팀 API DATA\production-performance\production_performance.sqlite`
- 실시간 실적 반영: `C:\똑딱이 생산3팀 API DATA\live-production-need\current_production_need.sqlite`
- 생산실적 범위: 실행일 기준 전월 1일부터 오늘까지, S관 5개 생산공정
- 각 데이터 폴더의 `raw_api`, `backup`, `snapshot`에 원본·백업·갱신상태를 보관합니다.

```powershell
python collectors\process_status_collector.py
python collectors\production_performance_collector.py
python collectors\live_production_need_collector.py
```

`실시간 실적 반영`은 새 APS 회차를 감지하면 5개 공정창고의 WIP 원천 회차를
1분마다 가볍게 확인합니다. 5개 회차가 모두 새 값으로 일치한 시점에 전체
WIP·재고·완료실적을 수집해 기준선으로 저장합니다. 이후 한 시간 주기 또는 수동 갱신 때 같은 LOT·공정의
창고 입고와 완료실적 중 큰 수량을 한 번만 인정하고, 납기일 우선순위로 APS
부족량에서 차감합니다. 새 APS 회차가 확인되면 다시 APS 부족량으로 초기화합니다.
LOT 작업순서는 이 실시간 계산이 성공한 뒤 같은 계산 DB를 읽어 표시합니다.

프로그램을 계속 켜 둔 경우에는 저장된 자동 주기를 따릅니다. 종료 후 다시 켜면
2초 뒤 마지막 수집 시각을 확인해 누락분을 보완하며, 화면과 메뉴는 기존 로컬
자료로 즉시 사용할 수 있습니다. 전체 보완 순서는 BOM → APS → 생산실적 →
실시간 재고·완료실적 계산으로, 오래 걸리는 수집은 마지막 백그라운드 단계에서
실행합니다. 7일을 넘겨 다시 실행한 경우 생산실적은 전월부터 오늘까지 다시
구성해 중간 날짜 공백을 방지합니다.
