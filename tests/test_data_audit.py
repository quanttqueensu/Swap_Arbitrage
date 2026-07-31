from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.data_audit import (
    KeyRule,
    ProfileFailure,
    SourceChangedError,
    discover_artifacts,
    profile_artifacts,
    profile_csv,
    read_raw_header,
    sha256_file,
    validate_paths,
    verify_unchanged,
)


class AuditBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.repo_root = self.root / "repo"
        self.data_root = self.root / "working"
        self.repo_root.mkdir()
        (self.data_root / "data" / "nested").mkdir(parents=True)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_outputs_must_stay_under_repo_root(self) -> None:
        outside = self.root / "outside.md"
        with self.assertRaisesRegex(ValueError, "output must be inside repo root"):
            validate_paths(
                self.repo_root,
                self.data_root,
                outside,
                self.repo_root / "lineage.csv",
            )

    def test_discovers_data_csvs_and_top_level_r2_manifest_in_stable_order(self) -> None:
        (self.data_root / "data" / "z.csv").write_text("z\n1\n", encoding="utf-8")
        (self.data_root / "data" / "nested" / "a.csv").write_text(
            "a\n2\n", encoding="utf-8"
        )
        (self.data_root / "r2_objects.csv").write_text("object_key\nx\n", encoding="utf-8")
        self.assertEqual(
            [path.relative_to(self.data_root).as_posix() for path in discover_artifacts(self.data_root)],
            ["data/nested/a.csv", "data/z.csv", "r2_objects.csv"],
        )

    def test_excludes_artifact_in_symlinked_data_directory(self) -> None:
        linked_target = self.root / "linked-data"
        linked_target.mkdir()
        (linked_target / "linked.csv").write_text("value\n1\n", encoding="utf-8")
        link = self.data_root / "data" / "linked"
        try:
            os.symlink(linked_target, link, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"Windows denied directory link creation: {error}")

        self.assertEqual(discover_artifacts(self.data_root), [])

    def test_rejects_a_symlinked_supplied_data_root(self) -> None:
        linked_target = self.root / "linked-root-target"
        (linked_target / "data").mkdir(parents=True)
        (linked_target / "data" / "source.csv").write_text("value\n1\n", encoding="utf-8")
        linked_root = self.root / "linked-working"
        try:
            os.symlink(linked_target, linked_root, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"Windows denied directory link creation: {error}")

        with self.assertRaisesRegex(ValueError, "data root must not be a symlink"):
            discover_artifacts(linked_root)

    def test_validate_paths_rejects_a_symlinked_data_root(self) -> None:
        linked_target = self.root / "linked-root-target"
        linked_target.mkdir()
        linked_root = self.root / "linked-working"
        try:
            os.symlink(linked_target, linked_root, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"Windows denied directory link creation: {error}")

        with self.assertRaisesRegex(ValueError, "data root must not be a symlink"):
            validate_paths(
                self.repo_root,
                linked_root,
                self.repo_root / "inventory.md",
                self.repo_root / "lineage.csv",
            )

    def test_excludes_a_symlinked_csv(self) -> None:
        linked_target = self.root / "linked.csv"
        linked_target.write_text("value\n1\n", encoding="utf-8")
        link = self.data_root / "data" / "linked.csv"
        try:
            os.symlink(linked_target, link)
        except OSError as error:
            self.skipTest(f"Windows denied file link creation: {error}")

        self.assertEqual(discover_artifacts(self.data_root), [])

    def test_excludes_a_symlinked_r2_manifest(self) -> None:
        linked_target = self.root / "linked-r2.csv"
        linked_target.write_text("object_key\nx\n", encoding="utf-8")
        link = self.data_root / "r2_objects.csv"
        try:
            os.symlink(linked_target, link)
        except OSError as error:
            self.skipTest(f"Windows denied file link creation: {error}")

        self.assertEqual(discover_artifacts(self.data_root), [])

    def test_reads_duplicate_raw_headers_without_normalizing_them(self) -> None:
        path = self.data_root / "data" / "duplicate.csv"
        path.write_text("date,value,value\n2026-01-01,1,1\n", encoding="utf-8")
        self.assertEqual(read_raw_header(path), ("date", "value", "value"))

    def test_detects_a_source_change_from_hash_snapshot(self) -> None:
        path = self.data_root / "data" / "source.csv"
        path.write_text("value\n1\n", encoding="utf-8")
        before = {path: sha256_file(path)}
        path.write_text("value\n2\n", encoding="utf-8")
        with self.assertRaisesRegex(SourceChangedError, "source changed during audit"):
            verify_unchanged(before)


class CsvProfilingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_profiles_headers_rows_dates_keys_missing_constants_and_duplicates(self) -> None:
        path = self.root / "sample.csv"
        path.write_text(
            "date,id,value,copy,constant\n"
            "2026-01-02,a,1,1,x\n"
            "2026-01-01,a,,2,x\n"
            "2026-01-03,b,3,3,x\n",
            encoding="utf-8",
        )
        profile = profile_csv(path, self.root, KeyRule(("date", "id")))
        self.assertEqual(profile.relative_path, "sample.csv")
        self.assertEqual(profile.row_count, 3)
        self.assertEqual((profile.start_time, profile.end_time), ("2026-01-01", "2026-01-03"))
        self.assertEqual(profile.sort_order, "unsorted")
        self.assertEqual(profile.duplicate_key_count, 0)
        self.assertEqual(profile.columns[2].missing_count, 1)
        self.assertTrue(profile.columns[4].constant)
        self.assertEqual(profile.duplicate_column_pairs, ())
        self.assertEqual(profile.scan_mode, "full")

    def test_detects_duplicate_headers_keys_and_equal_columns_by_ordinal(self) -> None:
        path = self.root / "duplicates.csv"
        path.write_text(
            "date,id,value,value\n"
            "2026-01-01,a,1,1\n"
            "2026-01-01,a,2,2\n",
            encoding="utf-8",
        )
        profile = profile_csv(path, self.root, KeyRule(("date", "id")))
        self.assertEqual(profile.duplicate_headers, ("value",))
        self.assertEqual(profile.duplicate_key_count, 2)
        self.assertEqual(profile.duplicate_column_pairs, ((2, 3),))

    def test_large_file_mode_is_labelled_sampled(self) -> None:
        path = self.root / "large.csv"
        path.write_text("date,value\n2026-01-01,1\n2026-01-02,2\n", encoding="utf-8")
        profile = profile_csv(path, self.root, KeyRule(("date",)), full_scan_limit_bytes=1)
        self.assertEqual(profile.scan_mode, "sampled:first-and-last-1000-rows")
        self.assertEqual(profile.row_count, 2)

    def test_malformed_file_becomes_failure_without_losing_valid_profile(self) -> None:
        good = self.root / "good.csv"
        bad = self.root / "bad.csv"
        good.write_text("date,value\n2026-01-01,1\n", encoding="utf-8")
        bad.write_bytes(b"date,value\n\xff")
        results = profile_artifacts([bad, good], self.root, {})
        self.assertIsInstance(results[0], ProfileFailure)
        self.assertEqual(results[0].relative_path, "bad.csv")
        self.assertEqual(results[1].relative_path, "good.csv")

    def test_empty_file_becomes_failure_without_losing_valid_profile(self) -> None:
        empty = self.root / "empty.csv"
        good = self.root / "good.csv"
        empty.write_text("", encoding="utf-8")
        good.write_text("date,value\n2026-01-01,1\n", encoding="utf-8")
        results = profile_artifacts([good, empty], self.root, {})
        self.assertIsInstance(results[0], ProfileFailure)
        self.assertEqual(results[0].relative_path, "empty.csv")
        self.assertEqual(results[0].error_type, "ValueError")
        self.assertEqual(results[1].relative_path, "good.csv")

    def test_sampled_file_rejects_malformed_width_in_unsaved_middle(self) -> None:
        path = self.root / "sampled-malformed.csv"
        rows = ["date,value"]
        rows.extend(f"2026-01-{index:04d},{index}" for index in range(1, 1001))
        rows.append("2026-01-1001")
        rows.extend(f"2026-01-{index:04d},{index}" for index in range(1002, 2002))
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "row width differs from header width"):
            profile_csv(path, self.root, full_scan_limit_bytes=1)


if __name__ == "__main__":
    unittest.main()


from tools.data_audit import SourceEvidence, build_lineage, scan_source_evidence


class LineageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        (self.root / "tools").mkdir()
        (self.root / "data").mkdir()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _git(self, *arguments: str) -> None:
        subprocess.run(
            ["git", *arguments],
            cwd=self.root,
            check=True,
            capture_output=True,
            text=True,
        )

    def test_source_scan_records_exact_file_and_line_evidence(self) -> None:
        (self.root / "writer.py").write_text(
            'OUTPUT = "signal_data.csv"\n'
            'frame = pd.read_csv("raw_price_data.csv")\n'
            'frame["signal"] = frame["price"]\n'
            'frame.to_csv(OUTPUT)\n',
            encoding="utf-8",
        )
        self._git("init", "-q")
        self._git("add", "writer.py")
        evidence = scan_source_evidence(
            self.root,
            {"raw_price_data.csv", "signal_data.csv", "signal", "price"},
        )
        self.assertEqual(evidence["signal_data.csv"][0].location, "writer.py:1")
        self.assertEqual(evidence["signal"][0].location, "writer.py:3")
        self.assertEqual(evidence["__csv_read__"][0].location, "writer.py:2")
        self.assertEqual(evidence["__csv_write__"][0].location, "writer.py:4")

    def test_source_scan_uses_only_tracked_python_files(self) -> None:
        (self.root / "tracked.py").write_text('TOKEN = "tracked"\n', encoding="utf-8")
        (self.root / "scratch.py").write_text('TOKEN = "untracked"\n', encoding="utf-8")
        (self.root / ".gitignore").write_text("ignored.py\n", encoding="utf-8")
        (self.root / "ignored.py").write_text('TOKEN = "ignored"\n', encoding="utf-8")
        self._git("init", "-q")
        self._git("add", "tracked.py", ".gitignore")

        evidence = scan_source_evidence(self.root, {"tracked", "untracked", "ignored"})

        self.assertEqual(evidence["tracked"][0].location, "tracked.py:1")
        self.assertEqual(evidence["untracked"], ())
        self.assertEqual(evidence["ignored"], ())

    def test_source_scan_skips_tracked_python_deleted_from_worktree(self) -> None:
        deleted = self.root / "deleted.py"
        deleted.write_text('TOKEN = "deleted"\n', encoding="utf-8")
        self._git("init", "-q")
        self._git("add", "deleted.py")
        deleted.unlink()

        evidence = scan_source_evidence(self.root, {"deleted"})

        self.assertEqual(evidence["deleted"], ())

    def test_lineage_covers_every_ordinal_and_marks_copied_unused_columns(self) -> None:
        raw = self.root / "raw_price_data.csv"
        signal = self.root / "signal_data.csv"
        risk = self.root / "risk_data.csv"
        backtest = self.root / "swap_arb_backtest_case.csv"
        raw.write_text("date,price,orphan\n2026-01-01,100,x\n", encoding="utf-8")
        signal.write_text("date,price,orphan,signal\n2026-01-01,100,x,1\n", encoding="utf-8")
        risk.write_text("date,price,orphan,signal,risk_allowed\n2026-01-01,100,x,1,True\n", encoding="utf-8")
        backtest.write_text("date,signal,risk_allowed,daily_pnl\n2026-01-01,1,True,5\n", encoding="utf-8")
        profiles = [
            profile_csv(path, self.root)
            for path in (raw, signal, risk, backtest)
        ]
        rows = build_lineage(profiles, {})
        self.assertEqual(len(rows), 3 + 4 + 5 + 4)
        orphan = next(row for row in rows if row.artifact == "risk_data.csv" and row.column == "orphan")
        self.assertEqual(orphan.classification, "unused")
        self.assertEqual(orphan.source_or_derivation, "copied from signal_data.csv:orphan")
        pnl = next(row for row in rows if row.column == "daily_pnl")
        self.assertEqual((pnl.classification, pnl.unit, pnl.status), ("accounting", "usd", "verified"))

    def test_unknown_derived_column_is_visible_as_a_discrepancy(self) -> None:
        path = self.root / "signal_data.csv"
        path.write_text("date,mystery\n2026-01-01,1\n", encoding="utf-8")
        rows = build_lineage([profile_csv(path, self.root)], {})
        mystery = next(row for row in rows if row.column == "mystery")
        self.assertEqual(mystery.status, "discrepancy")
        self.assertIn("no verified classification", mystery.evidence)

    def test_unknown_derived_column_with_source_evidence_is_a_discrepancy(self) -> None:
        path = self.root / "signal_data.csv"
        path.write_text("date,mystery\n2026-01-01,1\n", encoding="utf-8")
        evidence = {"mystery": (SourceEvidence("mystery", "writer.py:1", 'x = "mystery"'),)}

        row = next(row for row in build_lineage([profile_csv(path, self.root)], evidence) if row.column == "mystery")

        self.assertEqual(row.status, "discrepancy")

    def test_copied_column_with_unknown_unit_is_a_discrepancy(self) -> None:
        raw = self.root / "raw_price_data.csv"
        signal = self.root / "signal_data.csv"
        raw.write_text("date,opaque\n2026-01-01,1\n", encoding="utf-8")
        signal.write_text("date,opaque\n2026-01-01,1\n", encoding="utf-8")
        profiles = [profile_csv(path, self.root) for path in (raw, signal)]

        row = next(row for row in build_lineage(profiles, {}) if row.artifact == "signal_data.csv" and row.column == "opaque")

        self.assertEqual(row.status, "discrepancy")

    def test_lineage_preserves_duplicate_backtest_basenames(self) -> None:
        first = self.root / "first" / "swap_arb_backtest_case.csv"
        second = self.root / "second" / "swap_arb_backtest_case.csv"
        first.parent.mkdir()
        second.parent.mkdir()
        first.write_text("date,daily_pnl\n2026-01-01,1\n", encoding="utf-8")
        second.write_text("date,daily_pnl\n2026-01-01,2\n", encoding="utf-8")

        rows = build_lineage(
            [profile_csv(path, self.root) for path in (first, second)],
            {},
        )

        self.assertEqual(len(rows), 4)
        self.assertEqual(
            {(row.artifact, row.ordinal) for row in rows},
            {
                ("first/swap_arb_backtest_case.csv", 0),
                ("first/swap_arb_backtest_case.csv", 1),
                ("second/swap_arb_backtest_case.csv", 0),
                ("second/swap_arb_backtest_case.csv", 1),
            },
        )
