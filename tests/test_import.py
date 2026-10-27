"""Directory import must exclude Finder ZIP metadata before reading DICOM."""
from pathlib import Path
import sys
import tempfile
import unittest

import pydicom
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import DigitalXRayImageStorageForPresentation, ExplicitVRLittleEndian, generate_uid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "PneumoconiosisAnnotator"))
from PneumoconiosisLib.core import scan_cases


class DirectoryImportTests(unittest.TestCase):
    def test_nested_unicode_archive_excludes_mac_metadata_even_if_it_contains_valid_dicom(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "Archive 胸片"
            path = root / "2023 Sample" / "病例 001" / "胸片.DCM"
            path.parent.mkdir(parents=True)
            meta = FileMetaDataset()
            meta.TransferSyntaxUID = ExplicitVRLittleEndian
            meta.MediaStorageSOPClassUID = DigitalXRayImageStorageForPresentation
            meta.MediaStorageSOPInstanceUID = generate_uid()
            ds = FileDataset(str(path), {}, file_meta=meta, preamble=b"\0" * 128)
            ds.SOPClassUID, ds.SOPInstanceUID = meta.MediaStorageSOPClassUID, meta.MediaStorageSOPInstanceUID
            ds.StudyInstanceUID, ds.SeriesInstanceUID = generate_uid(), generate_uid()
            ds.Modality, ds.Rows, ds.Columns = "DX", 8, 8
            ds.SamplesPerPixel, ds.PhotometricInterpretation = 1, "MONOCHROME2"
            ds.save_as(path, enforce_file_format=True)
            for helper in (root / "__MACOSX/2023 Sample/胸片.dcm", root / "._metadata/胸片.dcm",
                           path.parent / "._胸片.dcm"):
                helper.parent.mkdir(parents=True, exist_ok=True)
                # A different payload under the same UID would normally stop import.
                ds.Rows += 1
                ds.save_as(helper, enforce_file_format=True)
            (root / "__MACOSX/._病例001").write_bytes(b"\x00\x05\x16\x07AppleDouble")
            (root / ".DS_Store").write_bytes(b"Finder metadata")
            cases, _ = scan_cases(root)
            self.assertEqual(len(cases), 1)
            self.assertEqual(Path(cases[0]["file_path"]), path.resolve())
            self.assertEqual(cases[0]["rows"], 8)
            self.assertTrue((root / ".DS_Store").is_file())
            self.assertTrue((root / "__MACOSX/._病例001").is_file())
            for auxiliary in (root / "__MACOSX", root / "__MACOSX/2023 Sample", root / "._metadata"):
                with self.assertRaisesRegex(ValueError, "辅助目录"):
                    scan_cases(auxiliary)
