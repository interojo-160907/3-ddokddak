import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from services.backward_source import adapt, applicable_factories, outbound_state, supplement_order_quantities, validate_payload, SOURCE
from collectors import process_status_collector as collector
from collectors import live_production_need_collector as live
from collectors import aps_update_monitor as monitor
from services.process_status_service import ProcessStatusService
from services import dashboard_service


def sources():
    backward = dict(demand_id="ABC_O1_1_1", initial="ABC", demand_item_id="T1-01.00",
                    item_id="P1-01.00", due_date="2026-11-01", oper_cd="45", sale_cd="p1 ",
                    eff_fac_cd="04", res_site_id="", fac_basis="B", backward_qty=100, forward_qty=20)
    plan = dict(demand_id="ABC_O1_1_1", initial="ABC", so_id="O1", seq=1,
                demand_qty=3, pack_unit=30, demand_item_name="Sales name", item_id="P1-01.00",
                item_name="Process name", demand_type="이니셜", demand_group_id="1-Day_Sph")
    arrange = dict(sale_cd="P1", oper_id="45", fac_cd="04", nm_nm="Process name")
    return backward, plan, arrange


class BackwardMappingTests(unittest.TestCase):
    def test_left_join_and_machine_duplicates_do_not_multiply_quantity(self):
        b, p, a = sources()
        rows = adapt([b], [p, p], [a, a])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["plan_qty"], 100)
        self.assertEqual(rows[0]["_backward"]["res_site_id"], "")
        self.assertEqual(rows[0]["item_name"], "Process name")
        self.assertEqual(rows[0]["demand_item_name"], "Sales name")

    def test_unmatched_spec_preserved_common_only_and_no_quantity_copy(self):
        b, p, a = sources()
        b["demand_id"] = "ABC_O1_1_2"
        row = adapt([b], [p], [a])[0]
        self.assertEqual(row["pack_unit"], 30)
        self.assertIsNone(row.get("demand_qty"))
        self.assertEqual(row["plan_qty"], 100)

    def test_packaging_excluded_even_without_factory(self):
        b, p, a = sources()
        b.update(oper_cd="85", eff_fac_cd="")
        self.assertEqual(adapt([b], [p], [a]), [])

    def test_blank_site_shared_factory_keeps_full_quantity_once(self):
        b, p, a = sources()
        for effective in [None, "01", "04"]:
            b["eff_fac_cd"] = effective
            rows = adapt([b], [p], [a, a, dict(a, fac_cd="01")])
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["plan_qty"], 100)
            self.assertEqual(rows[0]["_metadata"]["applicable_factories"], ["01", "04"])

    def test_explicit_site_overrides_machine_registration_and_fallback(self):
        b, p, a = sources()
        for site in ["04", "S관(3공장)"]:
            b.update(res_site_id=site, eff_fac_cd="01")
            self.assertEqual(adapt([b], [p], [dict(a, fac_cd="01")])[0]["plan_qty"], 100)
            self.assertEqual(applicable_factories(b, {"01", "04"}), {"04"})
        for site in ["01", "02", "A관(1공장)", "C관(2공장)"]:
            b.update(res_site_id=site, eff_fac_cd="04")
            self.assertEqual(adapt([b], [p], [a]), [])

    def test_blank_site_without_s_registration_is_excluded(self):
        b, p, a = sources()
        for arrange in [[], [dict(a, fac_cd="01")]]:
            self.assertEqual(adapt([b], [p], arrange), [])

    def test_unknown_explicit_site_does_not_fall_back_to_registration(self):
        b, p, a = sources()
        b.update(res_site_id="unknown", eff_fac_cd=None)
        with self.assertRaises(RuntimeError):
            adapt([b], [p], [a])

    def test_conflicting_metadata_rejected(self):
        b, p, a = sources()
        with self.assertRaises(RuntimeError):
            adapt([b], [p, dict(p, pack_unit=90)], [a])

    def test_special_customers_remain_blank_and_priority_preserved(self):
        b, p, a = sources()
        for kind, rank in [("PB", 1), ("국내", 2), ("안전(국내)", 4), ("안전(해외)", 4)]:
            b.update(demand_id=kind + "_O1_1_1", initial=kind, cust_name="")
            row = adapt([b], [], [a])[0]
            self.assertEqual(row["cust_name"], "")
            self.assertEqual(row["_metadata"]["customer_status"], "not-required")
            self.assertEqual(live.allocation_priority(row)[1], rank)

    def test_exact_export_pcs_conversion_and_domestic_pack_quantity(self):
        b, p, a = sources()
        for order, endpoint, detail in [
            ("202609280392", "/api/order-status-detail", dict(od_no="202609280392", seq=1, gd_cd="T1-01.00", pack_qty=30, od_qty=10800)),
            ("R202610070001", "/api/prod-request-domestic", dict(re_no="R202610070001", re_seq=1, gd_cd="T1-01.00", pic_qty=30, re_pack_qty=360, re_pcs_qty=10800)),
        ]:
            b["demand_id"] = f"ABC_{order}_1_2"
            p["demand_id"] = f"ABC_{order}_1_1"
            rows = adapt([b], [p], [a])
            _, errors = supplement_order_quantities(rows, lambda *args: {"rows": [detail]}, "", 30)
            self.assertFalse(errors)
            self.assertEqual(rows[0]["demand_qty"], 360)
            self.assertEqual(rows[0]["_metadata"]["quantity_source"], endpoint)
            self.assertEqual(rows[0]["plan_qty"], 100)

    def test_unknown_order_pcs_is_not_zero(self):
        b, p, a = sources()
        b["demand_id"] = "ABC_O1_1_2"
        row = adapt([b], [p], [a])[0]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "aps.sqlite"
            with sqlite3.connect(path) as db:
                collector._initialize(db)
                db.execute(f"INSERT INTO aps_plan ({','.join(collector.FIELDS)},payload_json) VALUES ({','.join('?' for _ in range(len(collector.FIELDS)+1))})",
                           (*[row.get(f) for f in collector.FIELDS], json.dumps(row)))
            db.close()
            visible = ProcessStatusService(database_path=path).load_rows()
            self.assertIsNone(visible[0]["오더수량PCS"])
            self.assertEqual(visible[0]["하이드레이션"], 100)


class BackwardCycleTests(unittest.TestCase):
    def state(self):
        return {"source_kind": SOURCE, "source_identity": SOURCE + "|run|2026-10-12 07:26:00",
                "run_key": "run", "backward_at": "2026-10-12 07:26:00", "backward_count": 1,
                "plan_at": "2026-10-12 07:29:00", "plan_count": 1,
                "wip_at": "2026-10-12 07:20:00", "wip_count": 1}

    def test_truncated_or_count_mismatch_rejected(self):
        state = self.state()
        for payload in [{"rows": []}, {"rows": [{}], "source_refreshed_at": state["backward_at"], "truncated": True}]:
            with self.assertRaises(RuntimeError):
                validate_payload(payload, state, "backward")

    def test_running_source_not_collected(self):
        with self.assertRaises(RuntimeError):
            outbound_state({"rows": [{"tbl": "RUN", "run_state": "R", "run_key": "run"}]})

    def test_same_timestamp_plan_source_forces_first_recollection(self):
        state = self.state()
        with patch.object(monitor, "_local_status", return_value={"status": "success", "source_refreshed_at": state["backward_at"]}), \
             patch.object(monitor, "_request", return_value={}), \
             patch("services.backward_source.outbound_state", return_value=state), \
             patch.object(monitor, "refresh", return_value={"status": "success", "changed":True}) as refresh:
            self.assertTrue(monitor.check_and_refresh()["changed"])
            refresh.assert_called_once()

    def test_source_switch_accepts_matching_wip_even_if_earlier_and_unchanged(self):
        state = self.state()
        old = {"aps_source_refreshed_at": state["backward_at"], "wip_source_refreshed_at": state["wip_at"], "calculation_revision": 2}
        with patch.object(live, "_aps_cycle", return_value=state["backward_at"]), \
             patch.object(live, "_source_provenance", return_value=state), \
             patch.object(live, "_live_meta", return_value=old), \
             patch.object(live, "_read_json", return_value=old), \
             patch.object(live, "_aps_item_ids", return_value={"P1"}), \
             patch.object(live, "probe_wip_source", return_value={"ready": True, "source_refreshed_at": state["wip_at"]}), \
             patch.object(live, "_collect_wip", side_effect=RuntimeError("reached-full-wip")):
            with self.assertRaisesRegex(RuntimeError, "reached-full-wip"):
                live.refresh()

    def test_wrong_wip_preserves_previous_calculation(self):
        state = self.state()
        with patch.object(live, "_aps_cycle", return_value=state["backward_at"]), \
             patch.object(live, "_source_provenance", return_value=state), \
             patch.object(live, "_live_meta", return_value={}), \
             patch.object(live, "_read_json", return_value={}), \
             patch.object(live, "_aps_item_ids", return_value={"P1"}), \
             patch.object(live, "probe_wip_source", return_value={"ready": True, "source_refreshed_at": "2026-10-12 08:00:00"}), \
             patch.object(live, "_atomic_json"), \
             patch.object(live, "_collect_wip") as collect:
            self.assertEqual(live.refresh()["status"], "waiting_wip")
            collect.assert_not_called()

    def test_new_wip_for_same_backward_run_rebuilds_baseline(self):
        state=self.state()
        old={'aps_source_refreshed_at':state['backward_at'],'source_identity':state['source_identity'],
             'wip_source_refreshed_at':'2000-01-01 08:00:00','calculation_revision':2}
        with patch.object(live,'_aps_cycle',return_value=state['backward_at']), \
             patch.object(live,'_source_provenance',return_value=state), \
             patch.object(live,'_live_meta',return_value=old), \
             patch.object(live,'_read_json',return_value=old), \
             patch.object(live,'_aps_item_ids',return_value={'P1'}), \
             patch.object(live,'probe_wip_source',return_value={'ready':True,'source_refreshed_at':state['wip_at']}), \
             patch.object(live,'_collect_inventory',side_effect=AssertionError('WIP must be updated first')), \
             patch.object(live,'_collect_wip',side_effect=RuntimeError('new-wip-baseline')) as collect:
            with self.assertRaisesRegex(RuntimeError,'new-wip-baseline'):live.refresh()
            collect.assert_called_once()

    def test_mapping_revision_rebuilds_with_matching_unchanged_wip(self):
        state = self.state()
        old = {"source_kind": "aps-backward-plan:v1", "source_identity": "old-mapping",
               "wip_source_refreshed_at": state["wip_at"], "calculation_revision": 2}
        with patch.object(live, "_aps_cycle", return_value=state["backward_at"]), \
             patch.object(live, "_source_provenance", return_value=state), \
             patch.object(live, "_live_meta", return_value=old), \
             patch.object(live, "_read_json", return_value=old), \
             patch.object(live, "_aps_item_ids", return_value={"P1"}), \
             patch.object(live, "probe_wip_source", return_value={"ready": True, "source_refreshed_at": state["wip_at"]}), \
             patch.object(live, "_collect_wip", return_value=([], state["wip_at"], 0)), \
             patch.object(live, "_atomic_json"), \
             patch.object(live, "_collect_inventory", side_effect=RuntimeError("reached-inventory")):
            with self.assertRaisesRegex(RuntimeError, "reached-inventory"):
                live.refresh()

    def test_dashboard_rejects_plan_result_with_same_timestamp(self):
        state = self.state()
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / "live.sqlite"
            database.touch()
            with patch.object(dashboard_service, "LIVE_DB", database), \
                 patch.object(dashboard_service.safe_mode, "active", return_value=False), \
                 patch.object(dashboard_service, "_status", side_effect=[
                     {"source_refreshed_at": state["backward_at"], "source_identity": state["source_identity"]},
                     {"aps_source_refreshed_at": state["backward_at"], "status": "success"}]):
                self.assertFalse(dashboard_service._live_cycle_matches())

    def test_next_backward_run_waits_for_new_wip(self):
        state = self.state()
        previous = {"source_kind": SOURCE, "source_identity": "previous-backward-run",
                    "wip_source_refreshed_at": state["wip_at"], "calculation_revision": 2}
        with patch.object(live, "_aps_cycle", return_value=state["backward_at"]), \
             patch.object(live, "_source_provenance", return_value=state), \
             patch.object(live, "_live_meta", return_value=previous), \
             patch.object(live, "_read_json", return_value=previous), \
             patch.object(live, "_aps_item_ids", return_value={"P1"}), \
             patch.object(live, "probe_wip_source", return_value={"ready": True, "source_refreshed_at": state["wip_at"]}), \
             patch.object(live, "_atomic_json"), \
             patch.object(live, "_collect_wip") as collect:
            self.assertEqual(live.refresh()["status"], "waiting_wip")
            collect.assert_not_called()
