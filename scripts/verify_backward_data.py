"""Read isolated collected snapshots and replay live calculations without network."""
import os
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["DDOKDDAK_PROD3_PREVIEW"] = "1"
os.environ["DDOKDDAK_PROD3_PREVIEW_DATA_DIR"] = str(ROOT / "validation-data")
import gzip
import json
import sqlite3
import tempfile
from collections import defaultdict
from contextlib import closing
from unittest.mock import patch
from collectors import live_production_need_collector as live
from collectors import process_status_collector as process
from services.process_status_service import ProcessStatusService
from services.lot_work_order_service import LotWorkOrderService
from services.inventory_status_service import InventoryStatusService
from services.dashboard_service import DashboardService

ARTIFACTS = ROOT / "validation-artifacts"
ARTIFACTS.mkdir(exist_ok=True)
raw_path = sorted(process.RAW_DIR.glob("*.gz"))[-1]
raw = json.load(gzip.open(raw_path, "rt", encoding="utf-8"))
report = {"raw_snapshot": str(raw_path), "checks": {}}
expected = defaultdict(float)
for row in raw["payload"]["rows"]:
    if row.get("eff_fac_cd") == "04" and row.get("oper_cd") in {"10", "20", "45", "55", "80"}:
        expected[row["oper_cd"]] += row["backward_qty"]
with closing(sqlite3.connect(process.DB_PATH)) as db:
    totals = dict(db.execute("SELECT oper_id,SUM(plan_qty) FROM aps_plan GROUP BY oper_id"))
    assert dict(expected) == totals, (dict(expected), totals)
    report["backward_totals"] = totals
    report["rows"] = db.execute("SELECT COUNT(*) FROM aps_plan").fetchone()[0]
    report["blank_site_recovered"] = db.execute("SELECT COUNT(*) FROM backward_mapping WHERE TRIM(COALESCE(original_site,''))=''").fetchone()[0]
    report["unknown_order_qty"] = db.execute("SELECT COUNT(*) FROM aps_plan WHERE demand_qty IS NULL").fetchone()[0]
    report["missing_regular_customer"] = db.execute("SELECT COUNT(*) FROM aps_plan WHERE demand_type='이니셜' AND TRIM(COALESCE(cust_name,''))=''").fetchone()[0]
    report["missing_process_names"] = db.execute("SELECT COUNT(*) FROM aps_plan WHERE TRIM(COALESCE(item_name,''))=''").fetchone()[0]
    report["checks"]["original_quantity_not_multiplied"] = True
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

with closing(sqlite3.connect(live.DB_PATH)) as db:
    db.row_factory = sqlite3.Row
    tables = {name: [dict(row) for row in db.execute("SELECT * FROM " + name)]
              for name in ["baseline_wip", "current_inventory", "current_production"]}
    before = [tuple(r) for r in db.execute("SELECT id,plan_qty,original_plan_qty,allocated_qty FROM aps_plan ORDER BY id")]
    report["live_totals"] = [dict(row) for row in db.execute("SELECT oper_id,SUM(original_plan_qty) original,SUM(allocated_qty) allocated,SUM(plan_qty) remaining FROM aps_plan GROUP BY oper_id")]
    assert all(abs(r["original"] - r["allocated"] - r["remaining"]) < 0.001 for r in report["live_totals"])
    assert db.execute("SELECT COUNT(*) FROM aps_plan WHERE plan_qty<0 OR allocated_qty<0").fetchone()[0] == 0
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
with tempfile.TemporaryDirectory() as folder:
    temp = Path(folder) / "replayed.sqlite"
    live._recalculate_database(temp, tables["current_inventory"], tables["current_production"], "repeat", len(tables["current_inventory"]), len(tables["current_production"]))
    with closing(sqlite3.connect(temp)) as db:
        after = db.execute("SELECT id,plan_qty,original_plan_qty,allocated_qty FROM aps_plan ORDER BY id").fetchall()
    assert before == after, "Repeated evidence changed quantities"
    report["checks"]["repeat_evidence_no_double_deduction"] = True

status = live._read_json(live.STATUS_PATH)
with tempfile.TemporaryDirectory() as folder:
    target = Path(folder)
    prior = dict(status)
    prior.pop("source_identity", None)
    prior["source_kind"] = "aps-plan"
    prior["calculation_revision"] = live.CALCULATION_REVISION
    # Exact same APS/WIP timestamps, but the previous result was from Plan.
    with patch.multiple(live, DB_PATH=target/"live.sqlite", STATUS_PATH=target/"status.json", DATA_DIR=target, BACKUP_DIR=target/"backup"), \
         patch.object(live, "_live_meta", return_value=status), \
         patch.object(live, "_read_json", return_value=prior), \
         patch.object(live, "probe_wip_source", return_value={"ready": True, "source_refreshed_at": status["wip_source_refreshed_at"]}), \
         patch.object(live, "_collect_wip", return_value=(tables["baseline_wip"],status["wip_source_refreshed_at"],len(tables["baseline_wip"]))), \
         patch.object(live, "_collect_inventory", return_value=(tables["current_inventory"],len(tables["current_inventory"]))), \
         patch.object(live, "_collect_production", return_value=(tables["current_production"],len(tables["current_production"]))):
        switched = live.refresh()
        assert switched["reset"] and switched["status"] == "success"
        with closing(sqlite3.connect(target/"live.sqlite")) as db:
            switched_rows = db.execute("SELECT id,plan_qty,original_plan_qty,allocated_qty FROM aps_plan ORDER BY id").fetchall()
        assert before == switched_rows, "Source switch duplicated evidence deduction"
    report["checks"]["same_time_source_switch_reset_without_double_deduction"] = True

service = ProcessStatusService()
report["visible_process_rows"] = len(service.load_rows())
report["search_GG0270_rows"] = len(service.load_rows(search="GG0270"))
assert report["search_GG0270_rows"] > 0
report["lot_rows"] = {}
lot = LotWorkOrderService()
for process_name in ("사출", "분리", "하이드레이션", "접착", "누수규격"):
    rows = lot.load_rows(process=process_name)
    report["lot_rows"][process_name] = len(rows)
report["inventory"] = {k:v for k,v in InventoryStatusService().build({}).items() if k in ("products","rows","total80","unmapped")}
report["dashboard_available"] = DashboardService().load()["available"]
(ARTIFACTS / "data-verification.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(report,ensure_ascii=False,indent=2))

