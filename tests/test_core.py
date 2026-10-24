import csv
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "PneumoconiosisAnnotator"))
from PneumoconiosisLib.core import AnnotationStore, GRADES, GRADE_CODES, ZONES, empty_zones, validate_complete


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.store = AnnotationStore(self.directory)
        self.cases = [{"case_id": f"CXR-{i}", "source_sha256": str(i), "file_path": "/private/source.dcm"} for i in range(2)]
        self.store.import_cases(self.cases)

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def test_every_grade_survives_restart_as_text(self):
        for grade in GRADES:
            zones = {z: {"state": "scored", "grade": grade} for z in ZONES}
            self.store.save("CXR-0", "reader-01", zones, "", "complete")
            self.store.close()
            self.store = AnnotationStore(self.directory)
            self.assertEqual(self.store.get("CXR-0", "reader-01")["zones"], zones)

    def test_unread_and_not_scorable_are_not_zero(self):
        zones = empty_zones()
        zones["RU"] = {"state": "not_scorable", "grade": None}
        self.store.save("CXR-0", "reader-01", zones, "右上遮挡")
        read = self.store.get("CXR-0", "reader-01")
        self.assertIsNone(read["zones"]["RU"]["grade"])
        self.assertEqual(read["zones"]["RM"]["state"], "unread")
        with self.assertRaises(ValueError):
            validate_complete(zones, "右上遮挡")
        zones = {z: {"state": "not_scorable", "grade": None} for z in ZONES}
        with self.assertRaises(ValueError):
            validate_complete(zones, "")
        validate_complete(zones, "全片模糊")

    def test_readers_and_cases_remain_independent(self):
        zones = empty_zones()
        zones["LU"] = {"state": "scored", "grade": "2/3"}
        self.store.save("CXR-0", "reader-01", zones, "note")
        self.assertEqual(self.store.get("CXR-0", "reader-02")["zones"], empty_zones())
        self.assertEqual(self.store.get("CXR-1", "reader-01")["zones"], empty_zones())

    def test_invalid_input_never_overwrites_saved_record(self):
        self.store.save("CXR-0", "r", empty_zones(), "original")
        invalid = empty_zones()
        invalid["RU"] = {"state": "scored", "grade": "4/4"}
        with self.assertRaises(ValueError):
            self.store.save("CXR-0", "r", invalid, "bad")
        self.assertEqual(self.store.get("CXR-0", "r")["note"], "original")

    def test_failed_transaction_keeps_previous_rating_and_revision(self):
        self.store.save("CXR-0", "r", empty_zones(), "original")
        self.store.connection.executescript("CREATE TRIGGER reject_revision BEFORE INSERT ON revisions BEGIN SELECT RAISE(ABORT, 'forced disk failure'); END;")
        with self.assertRaises(sqlite3.DatabaseError):
            self.store.save("CXR-0", "r", empty_zones(), "lost?")
        self.assertEqual(self.store.get("CXR-0", "r")["note"], "original")
        self.assertEqual(self.store.connection.execute("SELECT count(*) FROM revisions").fetchone()[0], 1)

    def test_changed_source_is_rejected_atomically(self):
        changed = [dict(self.cases[0], source_sha256="changed"), self.cases[1]]
        with self.assertRaises(ValueError):
            self.store.import_cases(changed)
        row = self.store.connection.execute("SELECT metadata_json FROM cases WHERE case_id='CXR-0'").fetchone()
        self.assertEqual(json.loads(row[0])["source_sha256"], "0")

    def test_export_includes_unread_preserves_grades_and_protects_spreadsheets(self):
        zones = {z: {"state": "scored", "grade": GRADES[i]} for i, z in enumerate(ZONES)}
        self.store.save("CXR-0", "r", zones, "=HYPERLINK(\"example\")", "complete")
        paths = self.store.export(self.directory / "exports", [c["case_id"] for c in self.cases], "r")
        rows = list(csv.DictReader(paths[0].read_text(encoding="utf-8-sig").splitlines()))
        package = json.loads(paths[1].read_text())
        self.assertEqual(len(rows), 12)
        self.assertEqual(rows[0]["profusion_code"], GRADE_CODES["0/-"])
        self.assertTrue(rows[0]["note"].startswith("'="))
        self.assertEqual(package["records"][0]["zones"], zones)
        self.assertEqual(package["records"][1]["zones"], empty_zones())
        self.assertEqual(package["records"][0]["note"], '=HYPERLINK("example")')
        self.assertNotIn("/private/source", paths[1].read_text())
        self.assertEqual(len(package["history"]), 1)


if __name__ == "__main__":
    unittest.main()
