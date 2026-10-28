import csv
import json
import sqlite3
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "PneumoconiosisAnnotator"))
from PneumoconiosisLib.core import AnnotationStore, GRADES, GRADE_CODES, ZONES, completion_issues, empty_zones, polygon_issue, validate_complete


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.store = AnnotationStore(self.directory)
        self.cases = [{"case_id": f"CXR-{i}", "source_sha256": str(i), "file_path": "/private/source.dcm",
                       "source_relative_path": f"批次 {i}/胸片.dcm",
                       "rows": 3000, "columns": 2500} for i in range(2)]
        self.store.import_cases(self.cases)

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def test_every_grade_survives_restart_as_text(self):
        for grade in GRADES:
            zones = {z: {"state": "scored", "grade": grade} for z in ZONES}
            self.store.save("CXR-0", "reader-01", zones, "complete", quality={"grade": 1, "reason": ""})
            self.store.close()
            self.store = AnnotationStore(self.directory)
            self.assertEqual(self.store.get("CXR-0", "reader-01")["zones"], zones)

    def test_unread_and_not_scorable_are_not_zero(self):
        zones = empty_zones()
        zones["RU"] = {"state": "not_scorable", "grade": None}
        self.store.save("CXR-0", "reader-01", zones)
        read = self.store.get("CXR-0", "reader-01")
        self.assertIsNone(read["zones"]["RU"]["grade"])
        self.assertEqual(read["zones"]["RM"]["state"], "unread")
        with self.assertRaises(ValueError):
            validate_complete(zones)
        zones = {z: {"state": "not_scorable", "grade": None} for z in ZONES}
        validate_complete(zones)
        saved = self.store.save("CXR-0", "reader-01", zones, "complete", quality={"grade": 4})
        self.assertEqual(saved["status"], "complete")
        self.assertEqual(saved["audio"], [])
        self.assertNotIn("note", saved)

    def test_readers_and_cases_remain_independent(self):
        zones = empty_zones()
        zones["LU"] = {"state": "scored", "grade": "2/3"}
        self.store.save("CXR-0", "reader-01", zones)
        self.assertEqual(self.store.get("CXR-0", "reader-02")["zones"], empty_zones())
        self.assertEqual(self.store.get("CXR-1", "reader-01")["zones"], empty_zones())

    def test_completion_reports_all_missing_fields_and_preserves_optional_inputs(self):
        zones = empty_zones()
        zones["RU"] = {"state": "not_scorable", "grade": None}
        shape = self.spatial(points=[[100, 100, 0], [200, 200, 0]])
        pending = {"state": "pending_review"}
        issues = completion_issues(zones, None, [shape], pending, self.cases[0])
        self.assertEqual(set(issues), {"ratings", "quality", "spatial", "partition"})
        self.assertEqual(zones["RM"]["state"], "unread")
        for zone in ZONES[1:]:
            zones[zone] = {"state": "scored", "grade": "0/0"}
        issues = completion_issues(zones, {"grade": 4}, [self.spatial()], None, self.cases[0])
        self.assertEqual(issues, {})
        self.store.save("CXR-0", "reader-01", zones, "complete", quality={"grade": 4})
        self.assertEqual(self.store.get("CXR-0", "reader-01")["status"], "complete")

    def test_invalid_input_never_overwrites_saved_record(self):
        self.store.save("CXR-0", "r", empty_zones())
        invalid = empty_zones()
        invalid["RU"] = {"state": "scored", "grade": "4/4"}
        with self.assertRaises(ValueError):
            self.store.save("CXR-0", "r", invalid)
        self.assertEqual(self.store.get("CXR-0", "r")["zones"], empty_zones())

    def test_failed_transaction_keeps_previous_rating_and_revision(self):
        self.store.save("CXR-0", "r", empty_zones())
        self.store.connection.executescript("CREATE TRIGGER reject_revision BEFORE INSERT ON revisions BEGIN SELECT RAISE(ABORT, 'forced disk failure'); END;")
        changed = empty_zones()
        changed["RU"] = {"state": "scored", "grade": "1/0"}
        with self.assertRaises(sqlite3.DatabaseError):
            self.store.save("CXR-0", "r", changed)
        self.assertEqual(self.store.get("CXR-0", "r")["zones"], empty_zones())
        self.assertEqual(self.store.connection.execute("SELECT count(*) FROM revisions").fetchone()[0], 1)

    def test_changed_source_is_rejected_atomically(self):
        changed = [dict(self.cases[0], source_sha256="changed"), self.cases[1]]
        with self.assertRaises(ValueError):
            self.store.import_cases(changed)
        row = self.store.connection.execute("SELECT metadata_json FROM cases WHERE case_id='CXR-0'").fetchone()
        self.assertEqual(json.loads(row[0])["source_sha256"], "0")

    def test_export_includes_unread_preserves_grades_and_protects_spreadsheets(self):
        zones = {z: {"state": "scored", "grade": GRADES[i]} for i, z in enumerate(ZONES)}
        shape = dict(self.spatial(), label='=HYPERLINK("example")')
        self.store.save("CXR-0", "r", zones, "complete", quality={"grade": 1, "reason": ""}, spatial_annotations=[shape])
        bundle = self.store.export(self.directory / "exports", [c["case_id"] for c in self.cases]) / "r"
        paths = [bundle / name for name in ("r.csv", "r.json", "r-spatial.csv", "r-images.csv")]
        rows = list(csv.DictReader(paths[0].read_text(encoding="utf-8-sig").splitlines()))
        package = json.loads(paths[1].read_text())
        self.assertEqual(len(rows), 12)
        self.assertEqual(rows[0]["profusion_code"], GRADE_CODES["0/-"])
        vertices = list(csv.DictReader(paths[2].read_text(encoding="utf-8-sig").splitlines()))
        self.assertTrue(vertices[0]["label"].startswith("'="))
        self.assertNotIn("note", rows[0])
        self.assertEqual(package["records"][0]["zones"], zones)
        self.assertEqual(package["records"][1]["zones"], empty_zones())
        self.assertEqual(package["records"][0]["spatial_annotations"][0]["label"], '=HYPERLINK("example")')
        self.assertEqual(package["schema_version"], 6)
        self.assertNotIn("note", package["records"][0])
        self.assertNotIn("/private/source", paths[1].read_text())
        self.assertEqual(len(package["history"]), 1)

    def test_image_index_preserves_paths_after_restart_and_uses_only_current_cases(self):
        self.cases[1]["source_relative_path"] = '=原片, "一".dcm'
        self.store.import_cases(self.cases)
        self.store.save("CXR-1", "r", empty_zones(), quality={"grade": 1})
        self.store.close()
        self.store = AnnotationStore(self.directory)
        bundle = self.store.export(self.directory / "exports", ["CXR-1"]) / "r"
        paths = [bundle / name for name in ("r.csv", "r.json", "r-spatial.csv", "r-images.csv")]
        self.assertTrue(paths[3].name.endswith("-images.csv"))
        with paths[3].open(encoding="utf-8-sig", newline="") as stream:
            images = list(csv.DictReader(stream))
        self.assertEqual(images, [{"case_id": "CXR-1", "source_relative_path": './=原片, "一".dcm',
                                   "source_sha256": "1"}])
        with paths[0].open(encoding="utf-8-sig", newline="") as stream:
            scores = list(csv.DictReader(stream))
        self.assertEqual(len(scores), 6)
        self.assertEqual({row["case_id"] for row in scores}, {row["case_id"] for row in images})
        package = json.loads(paths[1].read_text(encoding="utf-8"))
        self.assertEqual(package["records"][0]["zones"], empty_zones())
        self.assertNotIn("source_relative_path", paths[1].read_text(encoding="utf-8"))
        self.assertNotIn("/private/source", paths[3].read_text(encoding="utf-8"))

    def test_missing_source_relative_path_stops_export_before_publishing_bundle(self):
        del self.cases[0]["source_relative_path"]
        self.store.import_cases(self.cases)
        self.store.save("CXR-0", "r", empty_zones(), quality={"grade": 1})
        destination = self.directory / "exports"
        with self.assertRaisesRegex(ValueError, "重新打开胸片目录"):
            self.store.export(destination, ["CXR-0"])
        self.assertEqual(list(destination.iterdir()), [])

    def test_export_groups_active_readers_and_ignores_navigation_and_other_cases(self):
        self.store.import_cases([dict(self.cases[0], case_id="CXR-outside")])
        self.store.save("CXR-0", "reader-01", empty_zones(), quality={"grade": 2})
        zones = empty_zones()
        zones["RU"] = {"state": "not_scorable", "grade": None}
        self.store.save("CXR-1", "reader-02", zones)
        self.store.save("CXR-0", "reader-03", empty_zones(), display={"window": 100, "level": 50})
        self.store.save("CXR-outside", "reader-04", empty_zones(), quality={"grade": 1})
        self.store.set_preference("reader", "reader-03")
        bundle = self.store.export(self.directory / "exports", ["CXR-0", "CXR-1"])
        self.assertRegex(bundle.name, r"^\d{8}-\d{6}-\d{6}$")
        self.assertEqual({p.name for p in bundle.iterdir()}, {"reader-01", "reader-02"})
        for reader in ("reader-01", "reader-02"):
            folder = bundle / reader
            self.assertEqual({p.name for p in folder.iterdir()},
                             {f"{reader}.csv", f"{reader}.json", f"{reader}-spatial.csv", f"{reader}-images.csv"})
            package = json.loads((folder / f"{reader}.json").read_text(encoding="utf-8"))
            self.assertEqual(package["reader_id"], reader)
            self.assertEqual([r["case_id"] for r in package["records"]], ["CXR-0", "CXR-1"])
            self.assertTrue(all(r["reader_id"] == reader for r in package["records"]))
            self.assertEqual(len(package["history"]), 1)
            self.assertEqual(package["history"][0]["annotation"]["reader_id"], reader)
            with (folder / f"{reader}.csv").open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 12)
            self.assertEqual({row["reader_id"] for row in rows}, {reader})
            if reader == "reader-01":
                self.assertEqual(package["records"][0]["quality"]["grade"], 2)
                self.assertEqual(package["records"][1]["zones"], empty_zones())
            else:
                self.assertIsNone(package["records"][0]["quality"]["grade"])
                self.assertEqual(package["records"][1]["zones"], zones)

    def test_export_retains_reader_whose_actual_annotation_remains_only_in_history(self):
        self.store.save("CXR-0", "reader-02", empty_zones(), spatial_annotations=[self.spatial()])
        self.store.save("CXR-0", "reader-02", empty_zones(), spatial_annotations=[])
        bundle = self.store.export(self.directory / "exports", ["CXR-0"])
        package = json.loads((bundle / "reader-02/reader-02.json").read_text(encoding="utf-8"))
        self.assertEqual(package["records"][0]["spatial_annotations"], [])
        self.assertEqual(len(package["history"]), 2)
        self.assertEqual(len(package["history"][0]["annotation"]["spatial_annotations"]), 1)

    def test_export_without_actual_reading_does_not_publish_an_empty_bundle(self):
        destination = self.directory / "exports"
        with self.assertRaisesRegex(ValueError, "还没有实际阅片记录"):
            self.store.export(destination, ["CXR-0"])
        self.store.save("CXR-0", "reader-01", empty_zones(), display={"window": 100, "level": 50})
        with self.assertRaisesRegex(ValueError, "还没有实际阅片记录"):
            self.store.export(destination, ["CXR-0"])
        self.assertEqual(list(destination.iterdir()), [])

    def spatial(self, kind="polygon", points=None):
        return {"id": uuid.uuid4().hex, "kind": kind, "label": "region",
                "points_ijk": points or [[100, 200, 0], [400, 250, 0], [200, 500, 0]]}

    def test_spatial_restart_reader_case_isolation_and_vertex_export(self):
        point = self.spatial("circle", [[-0.125, 350.5, 0], [20, 380, 0]])
        polygon = self.spatial()
        self.store.save("CXR-0", "r", empty_zones(), spatial_annotations=[point, polygon])
        self.store.close()
        self.store = AnnotationStore(self.directory)
        annotations = self.store.get("CXR-0", "r")["spatial_annotations"]
        self.assertEqual(annotations[0]["points_ijk"], point["points_ijk"])
        self.assertEqual(annotations[1]["state"], "ready")
        self.assertEqual(self.store.get("CXR-1", "r")["spatial_annotations"], [])
        self.assertEqual(self.store.get("CXR-0", "other")["spatial_annotations"], [])
        bundle = self.store.export(self.directory / "exports", ["CXR-0", "CXR-1"]) / "r"
        paths = [bundle / name for name in ("r.csv", "r.json", "r-spatial.csv", "r-images.csv")]
        vertices = list(csv.DictReader(paths[2].read_text(encoding="utf-8-sig").splitlines()))
        self.assertEqual(len(vertices), 5)
        self.assertEqual(float(vertices[0]["i"]), -0.125)
        self.assertEqual(vertices[0]["measurement_method"], "circle_diameter")
        self.assertEqual(vertices[0]["measurement_unit"], "px")
        self.assertAlmostEqual(float(vertices[0]["diameter"]), (20.125 ** 2 + 29.5 ** 2) ** .5)
        package = json.loads(paths[1].read_text())
        self.assertEqual(package["coordinate_system"]["index_base"], 0)
        self.assertEqual(package["records"][0]["image"]["measurement_scale"]["unit"], "px")
        self.assertEqual(package["records"][0]["spatial_annotations"], annotations)

    def test_unfinished_or_crossed_region_autosaves_as_draft_but_cannot_complete(self):
        zones = {z: {"state": "scored", "grade": "0/0"} for z in ZONES}
        for points in ([[100, 100, 0]], [[100, 100, 0], [200, 200, 0]],
                       [[100, 100, 0], [300, 300, 0], [100, 300, 0], [300, 100, 0]],
                       [[100, 100, 0], [200, 100, 0], [300, 100, 0]]):
            shape = self.spatial(points=points)
            saved = self.store.save("CXR-0", "r", zones, spatial_annotations=[shape])
            self.assertEqual(saved["spatial_annotations"][0]["state"], "draft")
            with self.assertRaises(ValueError):
                self.store.save("CXR-0", "r", zones, "complete", spatial_annotations=[shape], quality={"grade": 1, "reason": ""})
        self.assertIsNone(polygon_issue([[0, 0, 0], [100, 0, 0], [100, 100, 0], [0, 100, 0]]))

    def test_bad_spatial_coordinate_cannot_overwrite_and_deletion_is_audited(self):
        point = self.spatial("circle", [[100, 200, 0], [120, 240, 0]])
        self.store.save("CXR-0", "r", empty_zones(), spatial_annotations=[point])
        for coordinates in ([float("nan"), 200, 0], [2501, 200, 0], [100, -1, 0], [100, 200, 1]):
            invalid = dict(point, points_ijk=[coordinates])
            with self.assertRaises(ValueError):
                self.store.save("CXR-0", "r", empty_zones(), spatial_annotations=[invalid])
            self.assertEqual(self.store.get("CXR-0", "r")["spatial_annotations"][0]["points_ijk"], point["points_ijk"])
        self.store.save("CXR-0", "r", empty_zones(), spatial_annotations=[])
        self.assertEqual(self.store.get("CXR-0", "r")["spatial_annotations"], [])
        self.assertEqual(self.store.connection.execute("SELECT count(*) FROM revisions").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
