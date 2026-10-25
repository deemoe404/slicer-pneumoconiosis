import copy
import csv
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "PneumoconiosisAnnotator"))
from PneumoconiosisLib.assets import resolve_asset
from PneumoconiosisLib.audio_files import WaveRecording, pending_recordings
from PneumoconiosisLib.core import AnnotationStore, ZONES, empty_zones, validate_quality
from PneumoconiosisLib.partition import PARTITION_RULE, zone_map


class FeatureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.store = AnnotationStore(self.directory)
        self.cases = [{"case_id": "CXR-" + str(i), "source_sha256": "sha" + str(i), "columns": 100, "rows": 100} for i in range(2)]
        self.store.import_cases(self.cases)
        self.scored = {z: {"state": "scored", "grade": "1/0"} for z in ZONES}
        self.right = np.zeros((100, 100), dtype=bool)
        self.left = self.right.copy()
        self.right[10:90, 10:40] = True
        self.left[12:92, 60:90] = True
        self.landmarks = {"R": {"apex": [25, 10, 0], "dome": [25, 70, 0]},
                          "L": {"apex": [75, 12, 0], "dome": [75, 72, 0]}}

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def partition(self):
        reference = self.store.assets.put_lungs(self.right, self.left)
        return {"rule": PARTITION_RULE, "baseline": reference, "masks": reference,
                "baseline_landmarks": copy.deepcopy(self.landmarks), "landmarks": copy.deepcopy(self.landmarks),
                "model": {"weights_sha256": "verified-qa-fixture"}, "state": "pending_review", "confirmed_at": None,
                "source_sha256": "sha0"}

    def clip(self):
        writer = WaveRecording(self.directory, "CXR-0", "r", 16000)
        # Explicit synthetic PCM fixture; this test does not claim microphone capture.
        writer.write(np.arange(3200, dtype="<i2").tobytes())
        return writer.finish()

    def test_quality_all_four_levels_persist_and_blank_stays_unread(self):
        self.assertIsNone(self.store.get("CXR-0", "r")["quality"]["grade"])
        for grade in range(1, 5):
            record = self.store.save("CXR-0", "r", empty_zones(), "", quality={"grade": grade, "reason": "曝光偏白"})
            self.assertEqual(record["quality"], {"grade": grade, "reason": "曝光偏白"})
        self.assertIsNone(self.store.get("CXR-0", "other")["quality"]["grade"])
        self.assertIsNone(self.store.get("CXR-1", "r")["quality"]["grade"])
        for invalid in (0, 5, True, "1", 1.5):
            with self.assertRaises(ValueError):
                validate_quality({"grade": invalid})

    def test_completion_requires_quality_and_poor_quality_reason(self):
        with self.assertRaisesRegex(ValueError, "整片质量"):
            self.store.save("CXR-0", "r", self.scored, "", "complete")
        for grade in (3, 4):
            with self.assertRaisesRegex(ValueError, "质量缺陷"):
                self.store.save("CXR-0", "r", self.scored, "", "complete", quality={"grade": grade, "reason": ""})
        record = self.store.save("CXR-0", "r", self.scored, "", "complete", quality={"grade": 3, "reason": "吸气不足"})
        self.assertEqual(record["status"], "complete")

    def test_six_zones_use_apex_to_dome_and_cover_exactly_the_lung_masks(self):
        labels = zone_map(self.right, self.left, self.landmarks)
        self.assertEqual(labels[29, 25], 1)
        self.assertEqual(labels[30, 25], 2)
        self.assertEqual(labels[50, 25], 3)
        self.assertEqual(labels[89, 25], 3)  # Includes the costophrenic part below the dome.
        self.assertEqual(labels[32, 75], 5)
        np.testing.assert_array_equal(labels > 0, self.right | self.left)
        self.assertEqual(set(np.unique(labels)), set(range(7)))

    def test_manual_landmark_changes_move_the_zone_boundary_without_changing_masks(self):
        before = zone_map(self.right, self.left, self.landmarks)
        moved = copy.deepcopy(self.landmarks)
        moved["R"]["dome"][1] = 85
        after = zone_map(self.right, self.left, moved)
        self.assertEqual(before[32, 25], 2)
        self.assertEqual(after[32, 25], 1)
        np.testing.assert_array_equal(before > 0, after > 0)
        np.testing.assert_array_equal(before[:, 60:], after[:, 60:])

    def test_clinician_confirmation_and_source_binding_are_enforced(self):
        partition = self.partition()
        with self.assertRaisesRegex(ValueError, "确认分区"):
            self.store.save("CXR-0", "r", self.scored, "", "complete", quality={"grade": 1}, partition=partition)
        partition.update(state="confirmed", confirmed_at="2026-10-25T00:00:00+00:00", confirmed_by="r")
        record = self.store.save("CXR-0", "r", self.scored, "", "complete", quality={"grade": 1}, partition=partition)
        self.assertEqual(record["partition"]["state"], "confirmed")
        self.assertEqual(set(record["partition"]["pixel_counts"]), set("123456"))
        with self.assertRaisesRegex(ValueError, "原始胸片"):
            self.store.save("CXR-1", "r", empty_zones(), "", partition=partition)
        self.assertIsNone(self.store.get("CXR-0", "other")["partition"])
        with self.assertRaisesRegex(ValueError, "当前读者"):
            self.store.save("CXR-0", "other", empty_zones(), "", partition=partition)

    def test_corrected_masks_do_not_overwrite_retained_machine_result(self):
        partition = self.partition()
        corrected = self.right.copy()
        corrected[20:25, 10:15] = False
        partition["masks"] = self.store.assets.put_lungs(corrected, self.left)
        partition["state"] = "edited"
        saved = self.store.save("CXR-0", "r", empty_zones(), "", partition=partition)
        self.assertNotEqual(saved["partition"]["masks"], saved["partition"]["baseline"])
        np.testing.assert_array_equal(self.store.assets.lungs(partition["baseline"])[0], self.right)
        np.testing.assert_array_equal(self.store.assets.lungs(partition["masks"])[0], corrected)

    def test_bad_geometry_or_corrupt_asset_cannot_replace_a_saved_partition(self):
        partition = self.partition()
        original = self.store.save("CXR-0", "r", empty_zones(), "", partition=partition)
        invalid = copy.deepcopy(partition)
        invalid["landmarks"]["R"]["dome"][1] = 5
        with self.assertRaisesRegex(ValueError, "膈顶"):
            self.store.save("CXR-0", "r", empty_zones(), "", partition=invalid)
        self.assertEqual(self.store.get("CXR-0", "r")["partition"], original["partition"])
        resolve_asset(self.directory, partition["masks"]["path"]).write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "校验"):
            self.store.save("CXR-0", "r", empty_zones(), "", partition=partition)
        for path in ("../outside", "assets/../../outside", "/tmp/file", "C:\\file", "assets/../escape"):
            with self.assertRaises(ValueError):
                resolve_asset(self.directory, path)

    def test_recording_ownership_and_recovery_after_failed_database_write(self):
        clip = self.clip()
        with self.assertRaisesRegex(ValueError, "不属于"):
            self.store.save("CXR-0", "other", empty_zones(), "", audio=[clip])
        self.store.connection.executescript("CREATE TRIGGER reject_audio BEFORE INSERT ON annotations BEGIN SELECT RAISE(ABORT, 'QA failed write'); END;")
        with self.assertRaises(sqlite3.DatabaseError):
            self.store.save("CXR-0", "r", empty_zones(), "", audio=[clip])
        self.store.connection.execute("DROP TRIGGER reject_audio")
        self.assertEqual(self.store.recover_audio(), 1)
        self.assertEqual(self.store.get("CXR-0", "r")["audio"][0]["id"], clip["id"])
        self.assertEqual(self.store.recover_audio(), 0)

    def test_interrupted_wav_recovers_last_flushed_blocks(self):
        writer = WaveRecording(self.directory, "CXR-0", "r", 16000)
        writer.write(np.arange(1600, dtype="<i2").tobytes())
        writer.wave.close()
        writer.file.close()
        os.utime(writer.manifest, (1, 1))
        recordings = list(pending_recordings(self.directory))
        self.assertEqual(len(recordings), 1)
        self.assertEqual(recordings[0][2]["frames"], 1600)
        self.assertTrue(recordings[0][2]["interrupted"])

    def test_export_bundle_contains_real_wav_masks_zone_map_and_quality(self):
        clip, partition = self.clip(), self.partition()
        record = self.store.save("CXR-0", "r", empty_zones(), "", quality={"grade": 2, "reason": "轻微旋转"}, partition=partition, audio=[clip])
        paths = self.store.export(self.directory / "exports", ["CXR-0", "CXR-1"], "r")
        package = json.loads(paths[1].read_text())
        exported = package["records"][0]
        self.assertEqual(exported["quality"], record["quality"])
        self.assertEqual((paths[1].parent / clip["path"]).read_bytes(), resolve_asset(self.directory, clip["path"]).read_bytes())
        with np.load(paths[1].parent / exported["partition"]["zone_map"]["path"], allow_pickle=False) as data:
            np.testing.assert_array_equal(data["zones"], zone_map(self.right, self.left, self.landmarks))
        rows = list(csv.DictReader(paths[0].read_text(encoding="utf-8-sig").splitlines()))
        self.assertEqual(rows[0]["quality_grade"], "2")
        self.assertEqual(rows[0]["audio_count"], "1")
        self.assertEqual(rows[6]["quality_grade"], "")


if __name__ == "__main__":
    unittest.main()
