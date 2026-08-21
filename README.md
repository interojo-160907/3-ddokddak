# 똑딱이 생산3팀

생산3팀 납기 통합조회 프로그램의 PySide6 GUI 프로젝트입니다.

현재 단계에서는 GUI 골격과 왼쪽 사이드바 탐색만 구현되어 있으며 API, SQLite, Google Sheet 권한 기능은 연결하지 않았습니다.

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
- 생산실적 범위: 실행일 기준 전월 1일부터 오늘까지, S관 5개 생산공정
- 각 데이터 폴더의 `raw_api`, `backup`, `snapshot`에 원본·백업·갱신상태를 보관합니다.

```powershell
python collectors\process_status_collector.py
python collectors\production_performance_collector.py
```
