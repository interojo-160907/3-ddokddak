"""Use successful recovery timestamps consistently across collection indicators."""
from datetime import datetime


def recovered(error: dict, status: dict) -> bool:
    if status.get("status") not in {"success", "skipped"}:
        return False
    try:
        failed_at = datetime.fromisoformat(str(error["occurred_at"]))
        succeeded_at = datetime.fromisoformat(str(status.get("refreshed_at") or status.get("collected_at")))
        # Legacy GUI errors used naive local time; collector statuses include an offset.
        return succeeded_at.astimezone() > failed_at.astimezone()
    except (KeyError, TypeError, ValueError):
        return False


def collection_ready(statuses: dict, errors: dict, connections: dict) -> bool:
    return (
        not errors
        and all(statuses.get(key, {}).get("status") in {"success", "skipped"}
                for key in ("bom_status", "aps_status", "production_status", "live_status"))
        and all(connections.values())
    )


def header_status(statuses: dict, errors: dict, details: dict) -> tuple[str, str, bool]:
    from services.api_health import connection_label
    names = {"bom": "BOM", "aps": "APS", "production": "생산실적", "live": "재고"}
    collected = collection_ready(statuses, errors, {})
    failures = [(names.get(key, key), value) for key, value in details.items()
                if value.get("status") != "success"]
    descriptions = []
    for name, value in details.items():
        reason = value.get("error_type") or ("HTTP " + str(value["http_status"]) if value.get("http_status") else "")
        descriptions.append(f"{names.get(name, name)}: {connection_label(value)} · {value.get('elapsed_seconds', '-')}초 · {reason} · 연속 실패 {value.get('consecutive_failures', 0)}회 · 확인 {value.get('checked_at', '-')} · 마지막 정상 {value.get('last_success_at') or '-'}")
    if not collected:
        bad = [names.get(key.removesuffix('_status'), key.removesuffix('_status'))
               for key in ('bom_status', 'aps_status', 'production_status', 'live_status')
               if statuses.get(key, {}).get('status') not in {'success', 'skipped'}]
        descriptions.insert(0, '수집 결과 확인 필요: ' + ', '.join(bad or list(errors)))
        return "수집 상태 확인 필요", "\n".join(descriptions), False
    if failures:
        failures.sort(key=lambda item: connection_label(item[1]) == "응답 대기")
        name, result = failures[0]
        extra = f" 외 {len(failures)-1}건" if len(failures) > 1 else ""
        return f"수집 정상 · {name}{extra} {connection_label(result)}", "\n".join(descriptions), False
    if len(details) < 4:
        return "수집 정상 · 접속 확인 중", "마지막 수집 정상. 접속 점검 결과를 기다리는 중입니다.", False
    return "수집 전체 양호", "\n".join(descriptions), True
