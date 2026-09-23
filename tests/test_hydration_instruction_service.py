from __future__ import annotations

import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from services import hydration_instruction_service as module


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class _FixedDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 9, 18)


class HydrationInstructionServiceTests(unittest.TestCase):
    def test_placeholder_config_migrates_to_live_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder)
            (cache / "hydration_api_config.json").write_text(
                json.dumps({"endpoint": "", "lookback_days": 31}), encoding="utf-8"
            )

            config = module.HydrationInstructionService(cache).config()

            self.assertEqual(config["endpoint"], "/api/hydration-job-list")
            self.assertEqual(config["lookback_days"], 5)
            self.assertEqual(config["item_field"], "gd_cd")
            self.assertEqual(config["quantity_field"], "job_qty")

    def test_refresh_uses_today_inclusive_five_days_and_aggregates_products(self) -> None:
        payload = {
            "total_count": 2,
            "truncated": False,
            "rows": [
                {"job_no": "A1", "job_seq": 1, "gd_cd": "P100", "job_qty": 10},
                {"job_no": "A2", "job_seq": 1, "gd_cd": "P100", "job_qty": 15},
            ],
        }
        opened_urls: list[str] = []

        def fake_request(endpoint, params, timeout):
            self.assertEqual(timeout, 30)
            from urllib.parse import urlencode
            opened_urls.append(endpoint + '?' + urlencode(params))
            return payload

        with (
            tempfile.TemporaryDirectory() as folder,
            patch.object(module, "date", _FixedDate),
            patch.object(module, "request_json", side_effect=fake_request),
        ):
            service = module.HydrationInstructionService(Path(folder))
            result = service.refresh()
            snapshot = json.loads(service.current_path.read_text(encoding="utf-8"))

        self.assertEqual(result["rows"], 2)
        self.assertEqual(result["products"], 1)
        self.assertEqual(result["total_qty"], 25)
        self.assertEqual(snapshot["quantities"], {"P100": 25.0})
        self.assertIn("date_from=2026-09-14", opened_urls[0])
        self.assertIn("date_to=2026-09-18", opened_urls[0])
        self.assertIn("limit=0", opened_urls[0])

    def test_refresh_rejects_incomplete_or_duplicate_results(self) -> None:
        cases = [
            {"total_count": 2, "truncated": False, "rows": [{"gd_cd": "P1", "job_qty": 1}]},
            {
                "total_count": 2,
                "truncated": False,
                "rows": [
                    {"job_no": "A1", "job_seq": 1, "gd_cd": "P1", "job_qty": 1},
                    {"job_no": "A1", "job_seq": 1, "gd_cd": "P2", "job_qty": 2},
                ],
            },
        ]
        for payload in cases:
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as folder, patch.object(
                module,
                "request_json",
                return_value=payload,
            ):
                with self.assertRaises(ValueError):
                    module.HydrationInstructionService(Path(folder)).refresh()

    def test_invalid_quantity_does_not_publish_partial_totals(self):
        for quantity in ('NaN','Infinity',-1,'bad'):
            with self.subTest(quantity=quantity), tempfile.TemporaryDirectory() as folder:
                service=module.HydrationInstructionService(Path(folder))
                service.current_path.write_text('{"quantities":{"P1":99}}','utf-8')
                before=service.current_path.read_bytes()
                payload={'rows':[{'gd_cd':'P1','job_qty':10},{'gd_cd':'P2','job_qty':quantity}],'total_count':2}
                with patch.object(module,'request_json',return_value=payload), self.assertRaises(ValueError):
                    service.refresh()
                self.assertEqual(service.current_path.read_bytes(),before)

    def test_zero_instructions_replace_previous_quantity(self):
        with tempfile.TemporaryDirectory() as folder:
            service=module.HydrationInstructionService(Path(folder))
            service.current_path.write_text('{"quantities":{"P1":99}}','utf-8')
            with patch.object(module,'request_json',return_value={'rows':[],'total_count':0}):
                result=service.refresh()
            self.assertEqual(result['total_qty'],0)
            self.assertEqual(service.load()['quantities'],{})

    def test_wrong_process_or_date_preserves_previous_snapshot(self):
        for extra in ({'gong_cd':'55'}, {'job_dt':'2026-09-01'}):
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as folder:
                service=module.HydrationInstructionService(Path(folder))
                service.current_path.write_text('{"quantities":{"P1":99}}',encoding='utf-8')
                before=service.current_path.read_bytes()
                payload={'rows':[{'gd_cd':'P1','job_qty':1,**extra}],'total_count':1}
                with patch.object(module,'date',_FixedDate), patch.object(module,'request_json',return_value=payload), self.assertRaises(ValueError):
                    service.refresh()
                self.assertEqual(service.current_path.read_bytes(),before)

    def test_saved_dates_do_not_freeze_collection_period(self):
        with tempfile.TemporaryDirectory() as folder:
            service=module.HydrationInstructionService(Path(folder))
            cfg=service.config()
            cfg['params']={'date_from':'2020-01-01','date_to':'2020-01-05'}
            service.config_path.write_text(json.dumps(cfg),encoding='utf-8')
            with patch.object(module,'date',_FixedDate), patch.object(module,'request_json',return_value={'rows':[],'total_count':0}) as call:
                service.refresh()
            self.assertEqual(call.call_args.args[1]['date_from'],'2026-09-14')
            self.assertEqual(call.call_args.args[1]['date_to'],'2026-09-18')


if __name__ == "__main__":
    unittest.main()
