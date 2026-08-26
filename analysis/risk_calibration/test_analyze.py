import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from analysis.risk_calibration import analyze as analysis


class SemanticPathTests(unittest.TestCase):
    def test_uses_semantic_repository_paths(self):
        repository_root = Path(__file__).resolve().parents[2]
        self.assertEqual(
            analysis.SOURCE,
            repository_root
            / "data"
            / "raw"
            / "navigation_experiments"
            / "navigation_experiments.xlsx",
        )
        self.assertEqual(
            analysis.PROCESSED,
            repository_root / "data" / "processed" / "risk_calibration",
        )
        self.assertEqual(
            analysis.TABLES,
            repository_root / "results" / "risk_calibration" / "tables",
        )
        self.assertEqual(
            analysis.WEIGHT_VERSION,
            "entropy_cal35_seed20260822_fmax_po_unified_fbl",
        )


class OvershootRiskTests(unittest.TestCase):
    def _risk(self, *args):
        self.assertTrue(
            hasattr(analysis, "overshoot_risk"),
            "analysis must expose overshoot_risk for the safety-margin definition",
        )
        return analysis.overshoot_risk(*args)

    def test_exposes_overshoot_risk_calculator(self):
        self.assertTrue(
            hasattr(analysis, "overshoot_risk"),
            "analysis must expose overshoot_risk for the safety-margin definition",
        )

    def test_is_zero_until_walk_enters_safety_margin(self):
        self.assertEqual(self._risk("walk", 0.60, 1.00, 0.40), 0.0)

    def test_rises_linearly_with_fraction_of_margin_invaded(self):
        self.assertAlmostEqual(
            self._risk("walk", 0.70, 1.00, 0.40),
            0.25,
            places=12,
        )

    def test_is_one_when_candidate_reaches_or_passes_obstacle(self):
        self.assertEqual(self._risk("walk", 1.00, 1.00, 0.40), 1.0)
        self.assertEqual(self._risk("walk", 1.20, 1.00, 0.40), 1.0)

    def test_non_walk_and_zero_distance_actions_have_no_overshoot(self):
        self.assertEqual(self._risk("turn", 0.00, 0.20, 0.40), 0.0)
        self.assertEqual(self._risk("walk", 0.00, 0.20, 0.40), 0.0)

    def test_rejects_nonpositive_safety_margin(self):
        with self.assertRaises(ValueError):
            self._risk("walk", 0.50, 1.00, 0.00)


class BaselineRecalculationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = analysis.load_baseline().set_index("sample_id")

    def test_preserves_source_o_for_audit(self):
        self.assertIn("O_original", self.data.columns)

    def test_recomputes_front_distance_from_nav_json(self):
        self.assertIn("min_frontal_dist_recomputed", self.data.columns)

    def test_safe_walk_has_zero_o(self):
        row = self.data.loc["corridor_B_R01_S002"]
        self.assertEqual(row["proposed_move"], "walk")
        self.assertEqual(row["proposed_dist"], 2.0)
        self.assertEqual(row["min_frontal_dist"], 6.61)
        self.assertEqual(row["O"], 0.0)

    def test_partial_margin_intrusion_has_fractional_o(self):
        row = self.data.loc["corridor_B_R01_S019"]
        self.assertEqual(row["proposed_move"], "walk")
        self.assertEqual(row["proposed_dist"], 2.0)
        self.assertEqual(row["min_frontal_dist"], 2.09)
        self.assertAlmostEqual(row["O"], 0.775, places=12)

    def test_front_risk_is_maximum_of_p_and_o_for_every_action(self):
        self.assertIn("F", self.data.columns)
        expected = self.data[["P", "O"]].max(axis=1)
        pd.testing.assert_series_equal(
            self.data["F"], expected, check_names=False, check_exact=True
        )

    def test_turn_front_risk_degenerates_to_p(self):
        self.assertIn("F", self.data.columns)
        turn = self.data.loc[self.data["proposed_move"] == "turn"]
        pd.testing.assert_series_equal(
            turn["F"], turn["P"], check_names=False, check_exact=True
        )

    def test_exported_analysis_data_preserves_o_lineage(self):
        data, _ = analysis.assign_split(self.data.reset_index())
        weights = {"F": 0.4, "B": 0.25, "L": 0.35}
        original_processed = analysis.PROCESSED
        with TemporaryDirectory() as tmp:
            analysis.PROCESSED = Path(tmp)
            try:
                self.assertTrue(hasattr(analysis, "add_fbl_risk"))
                analysis.add_fbl_risk(data, weights)
                exported = pd.read_csv(
                    analysis.PROCESSED / "baseline_analysis_data.csv"
                )
            finally:
                analysis.PROCESSED = original_processed
        for column in (
            "O_original", "F", "min_frontal_dist", "min_frontal_dist_recomputed"
        ):
            self.assertIn(column, exported.columns)

    def test_component_change_summary_includes_walk_o(self):
        data, _ = analysis.assign_split(self.data.reset_index())
        original_tables = analysis.TABLES
        with TemporaryDirectory() as tmp:
            analysis.TABLES = Path(tmp)
            try:
                summary = analysis.save_component_change_summary(data)
            finally:
                analysis.TABLES = original_tables
        rows = summary.loc[
            (summary["action"] == "walk") & (summary["indicator"] == "O")
        ]
        self.assertEqual(len(rows), 1)


class UnifiedFblTests(unittest.TestCase):
    def _require(self, name):
        self.assertTrue(hasattr(analysis, name), f"missing analysis function: {name}")
        return getattr(analysis, name)

    def test_front_risk_uses_worse_of_state_and_action_risk(self):
        front_risk = self._require("front_risk")
        self.assertEqual(front_risk(0.2, 0.7), 0.7)
        self.assertEqual(front_risk(0.8, 0.1), 0.8)
        self.assertEqual(front_risk(0.4, 0.4), 0.4)

    def test_unified_fbl_risk_uses_same_formula_for_walk_and_turn(self):
        add_fbl_risk = self._require("add_fbl_risk")
        frame = pd.DataFrame({
            "proposed_move": ["walk", "turn"],
            "F": [0.8, 0.2], "B": [0.4, 0.6], "L": [0.1, 0.5],
        })
        result = add_fbl_risk(frame, {"F": 0.5, "B": 0.3, "L": 0.2}, export=False)
        self.assertAlmostEqual(result.loc[0, "risk_entropy"], 0.54, places=12)
        self.assertAlmostEqual(result.loc[1, "risk_entropy"], 0.38, places=12)

    def test_risk_distribution_summary_reports_action_quantiles(self):
        summarize = self._require("risk_distribution_summary")
        frame = pd.DataFrame({
            "data_split": ["calibration"] * 6,
            "proposed_move": ["walk"] * 4 + ["turn"] * 2,
            "risk_entropy": [0.0, 0.2, 0.8, 1.0, 0.1, 0.3],
        })
        summary = summarize(frame)
        walk = summary.loc[
            (summary["data_split"] == "calibration") &
            (summary["action"] == "walk")
        ].iloc[0]
        self.assertEqual(walk["n_steps"], 4)
        self.assertAlmostEqual(walk["p50"], 0.5, places=12)
        self.assertAlmostEqual(walk["p95"], 0.97, places=12)

    def test_histogram_counts_reconcile_to_each_action(self):
        histogram = self._require("risk_histogram_table")
        frame = pd.DataFrame({
            "data_split": ["calibration"] * 6,
            "proposed_move": ["walk"] * 4 + ["turn"] * 2,
            "risk_entropy": [0.0, 0.2, 0.8, 1.0, 0.1, 0.3],
        })
        table = histogram(frame, bins=5)
        totals = table.groupby(["data_split", "action"])["count"].sum()
        self.assertEqual(totals.loc[("calibration", "walk")], 4)
        self.assertEqual(totals.loc[("calibration", "turn")], 2)


if __name__ == "__main__":
    unittest.main()
