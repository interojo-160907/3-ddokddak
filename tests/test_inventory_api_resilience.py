from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.inventory_status_service import InventoryStatusService


class InventoryApiResilienceTests(unittest.TestCase):
    @staticmethod
    def _service(cache: Path) -> InventoryStatusService:
        service = InventoryStatusService.__new__(InventoryStatusService)
        service.cache = cache
        return service

    def test_successful_empty_warehouse_is_saved_as_zero_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            payload = {"total_count": 0, "rows": []}
            with patch("services.inventory_status_service.request_json", return_value=payload):
                result = self._service(cache)._request(
                    "inventory_사출창고", "/api/inventory-ledger-product", {"limit": 0}
                )

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["rows"], 0)
            saved = json.loads((cache / "inventory_사출창고.json").read_text("utf-8"))
            self.assertEqual(saved["rows"], [])

    def test_timeout_preserves_previous_file_and_stops_spec_queue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache=Path(directory)
            target=cache/'inventory_분리창고.json'
            target.write_text('{"rows":[{"stock_qty":10}]}', encoding='utf-8')
            before=target.read_bytes()
            service=self._service(cache)
            with patch('services.inventory_status_service.request_json', side_effect=TimeoutError('delayed')) as request:
                with self.assertRaises(TimeoutError):
                    service._request('inventory_분리창고','/api/inventory-ledger-product',{})
                self.assertEqual(target.read_bytes(),before)
                request.reset_mock()
                with self.assertRaises(TimeoutError):
                    service._warm_specs(['P'+str(i) for i in range(100)])
                self.assertLessEqual(request.call_count,2)


if __name__ == "__main__":
    unittest.main()
