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
            payload = io.BytesIO(json.dumps({"total_count": 0, "rows": []}).encode())
            with patch("services.inventory_status_service.urllib.request.urlopen", return_value=payload):
                result = self._service(cache)._request(
                    "inventory_사출창고", "/api/inventory-ledger-product", {"limit": 0}
                )

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["rows"], 0)
            saved = json.loads((cache / "inventory_사출창고.json").read_text("utf-8"))
            self.assertEqual(saved["rows"], [])

    def test_transient_timeout_retries_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            payload = io.BytesIO(json.dumps({"total_count": 0, "rows": []}).encode())
            with (
                patch(
                    "services.inventory_status_service.urllib.request.urlopen",
                    side_effect=[TimeoutError("temporary"), payload],
                ) as request,
                patch("services.inventory_status_service.time.sleep"),
            ):
                result = self._service(Path(directory))._request(
                    "inventory_분리창고", "/api/inventory-ledger-product", {"limit": 0}
                )

            self.assertEqual(result["status"], "success")
            self.assertEqual(request.call_count, 2)


if __name__ == "__main__":
    unittest.main()
