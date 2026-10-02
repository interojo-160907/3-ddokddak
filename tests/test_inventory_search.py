import collections
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from services import inventory_status_service as inventory


class InventorySearchTests(unittest.TestCase):
    def test_search_scopes_and_totals(self):
        with tempfile.TemporaryDirectory() as directory:
            service = inventory.InventoryStatusService(Path(directory))
            plans = []
            for power, order in [('-02.00', 'ORDER1'), ('+06.00', 'ORDER2')]:
                plans.append(dict(demand_id=order, so_id=order, initial='HW01',
                                  demand_type='', dest_country='', due_date='2026-12-31',
                                  oper_id='80', plan_qty=71, item_id='P1000'+power,
                                  item_name='TEST PRODUCT', demand_item_name='TEST PRODUCT',
                                  power=power, demand_group_id='1-Day_Sph'))
            items = [dict(gd_cd='P1000'+p, sale_cd='P1000', spec30=float(p), gd_nm='TEST PRODUCT')
                     for p in ('-02.00', '+06.00', '+05.75')]
            (Path(directory)/'item_P1000.json').write_text(json.dumps({'rows': items}))
            def read(path, tables):
                if 'aps_plan' in tables:
                    return {'aps_plan': plans, 'cycle_meta': []}
                return {'product_name_master': [dict(nm_cd='P1000', nm_nm='TEST PRODUCT', full_gu_nm='1-Day_Sph')], 'bom_relation': []}
            stocks = {wh: collections.Counter() for wh in inventory.WAREHOUSES}
            with patch.object(inventory, 'read_tables', side_effect=read), \
                 patch.object(service, '_stock_counts', return_value=(stocks, [])), \
                 patch.object(service.hydration_service, 'load', return_value={'status':'success','quantities':{}}):
                cases = [
                    ({'search':'-02.00'}, 1, 71),
                    ({'search':'P1000-02.00'}, 1, 71),
                    ({'search':'ORDER1'}, 1, 71),
                    ({'search':'HW01'}, 2, 142),
                    ({'search':'TEST PRODUCT'}, 3, 142),
                    ({'search':'P1000'}, 3, 142),
                    ({'search':'*'}, 3, 142),
                    ({'search':'-02.00,+06.00'}, 2, 142),
                    ({'search':'TEST PRODUCT','detail_search':'-02.00'}, 1, 71),
                    ({'detail_search':'-02.00，+06.00'}, 2, 142),
                    ({'search':'NOT FOUND'}, 0, 0),
                ]
                for filters, count, total in cases:
                    with self.subTest(filters=filters):
                        result = service.build(filters)
                        self.assertEqual(result['rows'], count)
                        self.assertEqual(result['total80'], total)
                        if count == 1:
                            self.assertEqual(result['sheets'][0]['products'][0]['rows'][0][5], 'P1000-02.00')

    def test_legacy_snapshot_key_is_invalidated(self):
        self.assertIn('"_search_schema":2', inventory.InventoryStatusService._filter_key({}))
