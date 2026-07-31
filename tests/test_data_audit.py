from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from tools.data_audit import (
    SourceChangedError,
    discover_artifacts,
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


if __name__ == "__main__":
    unittest.main()
