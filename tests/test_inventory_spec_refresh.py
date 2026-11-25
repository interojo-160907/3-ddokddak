import collections
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services import inventory_status_service as inventory
from collectors.delivery_refresh import calculation_failure


class SpecRefreshTests(unittest.TestCase):
    def run_build(self, directory, refresh, allow_network=True):
        service = inventory.InventoryStatusService(Path(directory))
        code = 'P5414A-09.50-0.75180'
        item = dict(gd_cd=code, sale_cd='P5414', spec30=-9.5, spec40=-.75, spec50=180)
        target = Path(directory)/'item_P5414.json'
        target.write_text(json.dumps({'rows': []}))
        plans = [dict(demand_id='one', item_id=code, oper_id='80', plan_qty=42,
                      due_date='2026-12-01')]
        def read(path, tables):
            if 'aps_plan' in tables:
                return {'aps_plan': plans, 'cycle_meta': []}
            return {'product_name_master': [], 'bom_relation': []}
        calls = []
        def warm(bases):
            if bases:
                calls.append(list(bases))
                if refresh: target.write_text(json.dumps({'rows': [item]}))
        stocks = {wh: collections.Counter() for wh in inventory.WAREHOUSES}
        with (patch.object(inventory, 'read_tables', side_effect=read),
              patch.object(service, '_warm_specs', side_effect=warm),
              patch.object(service, '_stock_counts', return_value=(stocks, [])),
              patch.object(service.hydration_service, 'load', return_value={'status':'success','quantities':{}})):
            if refresh:
                result = service.build(collecting=True, allow_network=allow_network)
                self.assertEqual(result['total80'], 42)
                self.assertEqual(result['rows'], 1)
            else:
                with self.assertRaisesRegex(ValueError, 'P5414A-09.50'):
                    service.build(collecting=True, allow_network=allow_network)
                self.assertEqual(list(service.view_cache.glob('*.json.gz')), [])
        self.assertEqual(calls, [['P5414']] if allow_network else [])

    def test_existing_stale_cache_refreshes_and_calculation_completes(self):
        with tempfile.TemporaryDirectory() as d: self.run_build(d, True)

    def test_missing_in_latest_source_still_blocks_publication(self):
        with tempfile.TemporaryDirectory() as d: self.run_build(d, False)

    def test_display_does_not_fetch_network(self):
        with tempfile.TemporaryDirectory() as d: self.run_build(d, False, False)

    def test_nested_calculation_error_is_retained(self):
        with tempfile.TemporaryDirectory() as d:
            stage=Path(d); settings=stage/'backward-v1/settings'; settings.mkdir(parents=True)
            (settings/'full_refresh_status.json').write_text(json.dumps(
                {'results': {'live': {'result': {'result': {'message': 'Missing P5414'}}}}}))
            self.assertEqual(calculation_failure(stage), 'Missing P5414')
