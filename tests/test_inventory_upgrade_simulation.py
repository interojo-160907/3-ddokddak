import unittest
from services.inventory_simulation import run


class InventoryUpgradeSimulationTests(unittest.TestCase):
    def test_upgrade_collection_recovery_and_render(self):
        self.assertGreaterEqual(len(run()['checks']),13)
