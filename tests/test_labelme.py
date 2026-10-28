"""Image fidelity, current-reader isolation and the real isolated export worker."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid

import numpy as np
from PIL import Image
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import DigitalXRayImageStorageForPresentation, ExplicitVRLittleEndian, generate_uid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "PneumoconiosisAnnotator"))
from PneumoconiosisLib.core import AnnotationStore, empty_zones, scan_cases
from PneumoconiosisLib.labelme_export import export_labelme, render_image
from PneumoconiosisLib.runtime import worker_command


def write_image(path, **values):
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = FileMetaDataset()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.MediaStorageSOPClassUID = DigitalXRayImageStorageForPresentation
    meta.MediaStorageSOPInstanceUID = generate_uid()
    ds = FileDataset(str(path), {}, file_meta=meta, preamble=b"\0" * 128)
    ds.SOPClassUID, ds.SOPInstanceUID = meta.MediaStorageSOPClassUID, meta.MediaStorageSOPInstanceUID
    ds.StudyInstanceUID, ds.SeriesInstanceUID = generate_uid(), generate_uid()
    ds.Modality, ds.Rows, ds.Columns = "DX", 8, 10
    ds.SamplesPerPixel, ds.PhotometricInterpretation = 1, "MONOCHROME2"
    ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 8, 8, 7, 0
    ds.WindowCenter, ds.WindowWidth = 128, 256
    pixels = np.arange(80, dtype=np.uint8).reshape(8, 10)
    ds.PixelData = pixels.tobytes()
    for key, value in values.items():
        setattr(ds, key, value)
    ds.save_as(path, enforce_file_format=True)
    return pixels


class LabelmeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.source = self.directory / "原片 输入"
        self.pixels = write_image(self.source / "批次 A/胸片.dcm")
        write_image(self.source / "批次 B/胸片.dcm")
        self.cases, _ = scan_cases(self.source)
        self.case = next(c for c in self.cases if "批次 A" in c["file_path"])
        self.store = AnnotationStore(self.directory / "project")
        self.store.import_cases(self.cases)
        self.ellipse = {"id": str(uuid.uuid4()), "kind": "ellipse", "label": "圆形小阴影 一",
                      "points_ijk": [[-.125, 3.5, 0], [2, 6, 0]]}
        self.polygon = {"id": str(uuid.uuid4()), "kind": "polygon", "label": "区域",
                        "points_ijk": [[2, 1, 0], [8, 1, 0], [6, 6, 0], [2, 6, 0]]}
        self.draft = {"id": str(uuid.uuid4()), "kind": "polygon", "label": "未画完",
                      "points_ijk": [[4, 3, 0], [5, 4, 0]]}
        self.store.save(self.case["case_id"], "reader-01", empty_zones(),
                        spatial_annotations=[self.ellipse, self.polygon, self.draft])

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def snapshot(self):
        return self.store.export_snapshot([c["case_id"] for c in self.cases])

    def test_bundle_preserves_raster_coordinates_reader_isolation_and_snapshot(self):
        second = dict(self.ellipse, label="第二位读者", points_ijk=[[4, 3, 0], [8, 6, 0]])
        self.store.save(self.case["case_id"], "reader-02", empty_zones(), spatial_annotations=[second])
        self.store.save(self.case["case_id"], "reader-03", empty_zones(), display={"window": 10})
        snapshot = self.snapshot()
        self.store.save(self.case["case_id"], "reader-01", empty_zones(), spatial_annotations=[])
        events = []
        result = export_labelme(self.directory / "exports", snapshot, progress=lambda *p: events.append(p))
        bundle = Path(result["directory"])
        self.assertEqual(result["reader_count"], 2)
        self.assertEqual(result["skipped_count"], 1)
        self.assertEqual({p.name for p in bundle.iterdir()}, {"reader-01", "reader-02", "export-report.json"})
        self.assertEqual(events[-1], (6, 6))
        for reader in ("reader-01", "reader-02"):
            folder = bundle / reader
            self.assertEqual(len(list(folder.glob("*.json"))), 2)
            self.assertEqual(len(list(folder.glob("*.png"))), 2)
            path = folder / (self.case["case_id"] + ".json")
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertIsNone(data["imageData"])
            self.assertEqual((data["imageHeight"], data["imageWidth"]), (8, 10))
            self.assertEqual(data["reader_id"], reader)
            self.assertEqual(data["shapes"][0]["source_points_ijk"], (self.ellipse if reader == "reader-01" else second)["points_ijk"])
            self.assertEqual(len(data["shapes"][0]["points"]), 128)
            self.assertEqual(data["shapes"][0]["shape_type"], "polygon")
            if reader == "reader-01":
                self.assertEqual(data["shapes"][1]["points"], [p[:2] for p in self.polygon["points_ijk"]])
                self.assertTrue(data["flags"]["has_skipped_shapes"])
            with Image.open(folder / data["imagePath"]) as image:
                np.testing.assert_array_equal(np.asarray(image), self.pixels)
            unread = next(c for c in self.cases if c != self.case)
            empty = json.loads((folder / (unread["case_id"] + ".json")).read_text())
            self.assertEqual(empty["shapes"], [])
            self.assertFalse(empty["flags"]["case_complete"])
        text = (bundle / "export-report.json").read_text(encoding="utf-8")
        report = json.loads(text)
        self.assertNotIn(str(self.source), text)
        self.assertNotIn(self.case["sop_uid"], text)
        self.assertEqual({i["source_relative_path"] for i in report["images"]}, {"./批次 A/胸片.dcm", "./批次 B/胸片.dcm"})
        self.assertTrue(any(r["skipped_shapes"] for r in report["records"]))
        for case in self.cases:
            self.assertEqual(hashlib.sha256(Path(case["file_path"]).read_bytes()).hexdigest(), case["source_sha256"])

    def test_png_inversion_and_rescale_keep_stored_pixel_order(self):
        path = self.source / "批次 A/胸片.dcm"
        write_image(path, PhotometricInterpretation="MONOCHROME1", RescaleSlope=2, RescaleIntercept=-100,
                    WindowCenter=None, WindowWidth=None, PixelSpacing=[.2, .4])
        case = next(c for c in scan_cases(self.source)[0] if "批次 A" in c["file_path"])
        # Remove empty window tags so the declared fallback is used.
        import pydicom
        ds = pydicom.dcmread(path)
        del ds.WindowCenter, ds.WindowWidth
        ds.save_as(path, enforce_file_format=True)
        case["source_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        output = self.directory / "inverted.png"
        render_image(case, output)
        with Image.open(output) as image:
            np.testing.assert_array_equal(np.asarray(image), np.rint((1 - self.pixels / 79) * 255).astype(np.uint8))

    def test_changed_or_missing_source_never_publishes_a_bundle(self):
        snapshot = self.snapshot()
        original = Path(self.case["file_path"])
        original.write_bytes(original.read_bytes() + b"changed")
        destination = self.directory / "exports"
        with self.assertRaisesRegex(ValueError, "原始胸片内容已变化"):
            export_labelme(destination, snapshot)
        self.assertTrue(all(p.name.startswith(".partial-labelme-") for p in destination.iterdir()))
        original.unlink()
        with self.assertRaises(FileNotFoundError):
            export_labelme(destination, snapshot)
        self.assertTrue(all(p.name.startswith(".partial-labelme-") for p in destination.iterdir()))

    def test_bad_shape_cannot_silently_disappear(self):
        snapshot = copy.deepcopy(self.snapshot())
        record = next(r for r in snapshot["packages"][0]["records"] if r["case_id"] == self.case["case_id"])
        record["spatial_annotations"][0]["points_ijk"] = [[100, 200, 0]]
        with self.assertRaisesRegex(ValueError, "超出胸片边界"):
            export_labelme(self.directory / "exports", snapshot)

    def test_real_worker_exports_labelme_and_native_export_remains_independent(self):
        snapshot = self.snapshot()
        snapshot_path = self.directory / "snapshot.json"
        snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
        result = subprocess.run(worker_command(ROOT, "labelme", {"snapshot": str(snapshot_path),
                                "destination": str(self.directory / "labelme")}), text=True,
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        events = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(events[-1]["event"], "exported")
        self.assertTrue((Path(events[-1]["directory"]) / "reader-01" / (self.case["case_id"] + ".json")).is_file())
        native = self.store.export(self.directory / "native", [c["case_id"] for c in self.cases])
        self.assertTrue((native / "reader-01/reader-01.csv").is_file())
        self.assertEqual(list(native.rglob("*.png")), [])
