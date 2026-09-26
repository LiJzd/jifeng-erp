import unittest

from jifeng_erp.costing import CostingError, calculate


class TestCosting(unittest.TestCase):
    def test_partial_rework_and_loss_without_double_material_cost(self):
        result = calculate(
            100, 100, 50, 0,
            [
                {"receipt_type": "首次回货", "received_qty": 50, "good_qty": 50},
                {"receipt_type": "首次回货", "received_qty": 50, "rework_qty": 50},
                {"receipt_type": "返工回货", "received_qty": 50, "good_qty": 48,
                 "lost_qty": 2, "extra_processing_amount": 20},
            ],
        )
        self.assertTrue(result["is_complete"])
        self.assertEqual(result["good_qty"], "98")
        self.assertEqual(result["lost_qty"], "2")
        self.assertEqual(result["total_cost"], "170")
        self.assertEqual(result["unallocated_cost"], "0")
        self.assertEqual(sum(float(x["estimated_cost"]) for x in result["allocations"]), 170)

    def test_incomplete_order_keeps_unallocated_cost_visible(self):
        result = calculate(100, 100, 0, 0, [
            {"receipt_type": "首次回货", "received_qty": 50, "good_qty": 50},
        ])
        self.assertEqual(result["estimated_unit_cost"], "1")
        self.assertEqual(result["unreceived_qty"], "50")
        self.assertEqual(result["unallocated_cost"], "50")
        self.assertFalse(result["is_complete"])

    def test_rework_cannot_exceed_outstanding_rework(self):
        with self.assertRaisesRegex(CostingError, "超过待返工"):
            calculate(100, 100, 0, 0, [
                {"receipt_type": "返工回货", "received_qty": 1, "good_qty": 1},
            ])

    def test_each_receipt_must_balance(self):
        with self.assertRaisesRegex(CostingError, "数量之和"):
            calculate(100, 100, 0, 0, [
                {"receipt_type": "首次回货", "received_qty": 10, "good_qty": 9},
            ])

    def test_non_finite_and_negative_costs_are_rejected(self):
        for invalid in ("NaN", "Infinity", -1):
            with self.subTest(invalid=invalid), self.assertRaises(CostingError):
                calculate(100, invalid, 0, 0, [])

    def test_rounding_never_allocates_more_than_total(self):
        result = calculate(100, "0.99", 0, 0, [
            {"receipt_type": "首次回货", "received_qty": 1, "good_qty": 1}
            for _ in range(100)
        ])
        self.assertEqual(result["allocated_good_cost"], "0.99")
        self.assertEqual(result["unallocated_cost"], "0")

    def test_pending_inspection_can_be_resolved_without_editing_history(self):
        result = calculate(100, 100, 0, 0, [
            {"receipt_type": "首次回货", "received_qty": 100, "pending_qty": 100},
            {"receipt_type": "待检处理", "received_qty": 100, "good_qty": 90, "rework_qty": 10},
            {"receipt_type": "返工回货", "received_qty": 10, "good_qty": 9, "lost_qty": 1},
        ])
        self.assertEqual(result["good_qty"], "99")
        self.assertEqual(result["pending_qty"], "0")
        self.assertEqual(result["unallocated_cost"], "0")
        self.assertTrue(result["is_complete"])

    def test_all_lost_cost_remains_unallocated(self):
        result = calculate(100, 100, 0, 0, [
            {"receipt_type": "首次回货", "received_qty": 100, "lost_qty": 100},
        ])
        self.assertEqual(result["estimated_unit_cost"], "0")
        self.assertEqual(result["unallocated_cost"], "100")


if __name__ == "__main__":
    unittest.main()
