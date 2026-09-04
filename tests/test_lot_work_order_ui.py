import unittest

from ui.lot_work_order_page import (
    HYDRATION_SINGLE_COLUMNS,
    HYDRATION_SPLIT_COLUMNS,
    HYDRATION_WIDTHS,
    normalize_lot_market_selection,
)
from ui.main_window import MainWindow


class LotWorkOrderUiTest(unittest.TestCase):
    def test_hydration_columns_put_p_code_before_lot_and_keep_quantity_compact(self) -> None:
        self.assertEqual(
            HYDRATION_SINGLE_COLUMNS[3:6],
            ["Q코드", "P코드", "체크시트(LOT)"],
        )
        self.assertEqual(HYDRATION_SINGLE_COLUMNS[-1], "LOT수량")
        self.assertEqual(HYDRATION_SPLIT_COLUMNS[-2:], ["LOT수량", "배정수량"])
        self.assertNotIn("필요수량", HYDRATION_SPLIT_COLUMNS)
        self.assertNotIn("초과배정", HYDRATION_SPLIT_COLUMNS)
        self.assertLessEqual(
            sum(HYDRATION_WIDTHS[column] for column in HYDRATION_SPLIT_COLUMNS),
            1_550,
        )

    def test_internal_page_titles_only_show_process_names(self) -> None:
        self.assertEqual(
            MainWindow.LOT_PROCESS_TITLES,
            {
                "lot_injection": "사출",
                "lot_separation": "분리",
                "lot_hydration": "하이드레이션",
                "lot_inspection": "검사접착",
                "lot_leak": "누수규격",
            },
        )

    def test_all_four_market_buttons_equal_the_whole_lot_population(self) -> None:
        self.assertEqual(
            normalize_lot_market_selection({"해외", "PB", "국내", "안전"}),
            {"전체"},
        )
        self.assertEqual(
            normalize_lot_market_selection({"해외", "국내"}),
            {"해외", "국내"},
        )


if __name__ == "__main__":
    unittest.main()
