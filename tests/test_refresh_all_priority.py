from __future__ import annotations

import unittest
from unittest.mock import patch

from collectors import refresh_all


class RefreshAllPriorityTests(unittest.TestCase):
    def test_aps_failure_preserves_independent_collections_and_blocks_live(self):
        with (
            patch.object(refresh_all, 'refresh_aps', side_effect=RuntimeError('slow APS')),
            patch.object(refresh_all, 'refresh_bom', return_value={'status':'success'}) as bom,
            patch.object(refresh_all, 'refresh_production', return_value={'status':'success'}) as production,
            patch.object(refresh_all, 'refresh_live') as live,
            patch.object(refresh_all, '_write_result'), patch('builtins.print'),
        ):
            self.assertEqual(refresh_all.main(force_full=False), 1)
        bom.assert_called_once()
        production.assert_called_once()
        live.assert_not_called()

    def test_base_sources_settle_before_live_inventory_calculation(self) -> None:
        order: list[str] = []

        def collected(name: str):
            def run(*_args, **_kwargs):
                order.append(name)
                return {"status": "success"}

            return run

        with (
            patch.object(refresh_all, "refresh_bom", side_effect=collected("bom")),
            patch.object(refresh_all, "refresh_aps", side_effect=collected("aps")),
            patch.object(
                refresh_all,
                "refresh_production",
                side_effect=collected("production"),
            ),
            patch.object(refresh_all, "refresh_live", side_effect=collected("live")),
            patch.object(refresh_all, "_write_result"),
            patch("builtins.print"),
        ):
            self.assertEqual(refresh_all.main(), 0)

        self.assertEqual(order[:3], ["aps", "bom", "production"])
        self.assertEqual(order[-1], "live")


if __name__ == "__main__":
    unittest.main()
