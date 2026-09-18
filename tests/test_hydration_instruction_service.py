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

        def fake_urlopen(request, timeout):
            self.assertEqual(timeout, 120)
            opened_urls.append(request.full_url)
            return _Response(json.dumps(payload).encode("utf-8"))

        with (
            tempfile.TemporaryDirectory() as folder,
            patch.object(module, "date", _FixedDate),
            patch.object(module.urllib.request, "urlopen", side_effect=fake_urlopen),
            patch.object(module, "credential_value", return_value="test-key"),
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
                module.urllib.request,
                "urlopen",
                return_value=_Response(json.dumps(payload).encode("utf-8")),
            ):
                with self.assertRaises(ValueError):
                    module.HydrationInstructionService(Path(folder)).refresh()


if __name__ == "__main__":
    unittest.main()
