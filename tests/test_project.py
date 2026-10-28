import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "PneumoconiosisAnnotator"))
from PneumoconiosisLib.core import AnnotationStore, empty_zones
from PneumoconiosisLib.project import validate_project


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "标注项目"
        self.cases = [{"case_id": "CXR-a", "source_sha256": "a" * 64,
                       "file_path": "/original/a.dcm", "source_relative_path": "a.dcm",
                       "rows": 10, "columns": 10}]

    def tearDown(self):
        self.temporary.cleanup()

    def create(self):
        validate_project(self.project, self.cases, create=True)
        store = AnnotationStore(self.project)
        store.import_cases(self.cases)
        store.save("CXR-a", "reader-01", empty_zones(), quality={"grade": 2})
        store.close()

    def test_new_project_cannot_overwrite_existing_files(self):
        self.project.mkdir()
        (self.project / ".DS_Store").write_bytes(b"finder")
        validate_project(self.project, self.cases, create=True)
        (self.project / "notes.txt").write_text("keep me")
        with self.assertRaisesRegex(ValueError, "空文件夹"):
            validate_project(self.project, self.cases, create=True)
        self.assertEqual((self.project / "notes.txt").read_text(), "keep me")

    def test_open_never_creates_database_in_source_or_export_directory(self):
        self.project.mkdir()
        with self.assertRaisesRegex(ValueError, "没有标注项目"):
            validate_project(self.project, self.cases, create=False)
        self.assertEqual(list(self.project.iterdir()), [])

    def test_wrong_or_partial_dataset_is_rejected_without_modifying_project(self):
        self.create()
        original = (self.project / "annotations.sqlite3").read_bytes()
        for cases in ([], [dict(self.cases[0], source_sha256="changed")],
                      [dict(self.cases[0], case_id="other")], self.cases + [dict(self.cases[0], case_id="extra")]):
            with self.assertRaisesRegex(ValueError, "不匹配"):
                validate_project(self.project, cases, create=False)
        self.assertEqual((self.project / "annotations.sqlite3").read_bytes(), original)

    def test_relocated_images_and_project_keep_reader_progress(self):
        self.create()
        moved = self.root / "搬迁后的项目"
        shutil.move(self.project, moved)
        cases = [dict(self.cases[0], file_path="/new/place/renamed.dcm", source_relative_path="renamed.dcm")]
        validate_project(moved, cases, create=False)
        store = AnnotationStore(moved)
        store.import_cases(cases)
        self.assertEqual(store.get("CXR-a", "reader-01")["quality"]["grade"], 2)
        metadata = json.loads(store.connection.execute("SELECT metadata_json FROM cases").fetchone()[0])
        self.assertEqual(metadata["source_relative_path"], "renamed.dcm")
        store.close()

    def test_corrupt_database_is_not_initialized_as_new_project(self):
        self.project.mkdir()
        database = self.project / "annotations.sqlite3"
        database.write_bytes(b"not a database")
        with self.assertRaisesRegex(ValueError, "损坏"):
            validate_project(self.project, self.cases, create=False)
        self.assertEqual(database.read_bytes(), b"not a database")
