from __future__ import annotations

import os
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


if __name__ == "__main__":
    unittest.main()
