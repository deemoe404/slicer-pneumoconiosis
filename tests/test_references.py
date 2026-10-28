"""External reference scanning and DICOM presentation, without Slicer."""
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, SecondaryCaptureImageStorage, generate_uid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "PneumoconiosisAnnotator"))
from PneumoconiosisLib.references import read_reference, scan_references


def write_reference(path, bits=16, **values):
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = FileMetaDataset()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.MediaStorageSOPClassUID = SecondaryCaptureImageStorage
    meta.MediaStorageSOPInstanceUID = generate_uid()
    ds = FileDataset(str(path), {}, file_meta=meta, preamble=b"\0" * 128)
    ds.SOPClassUID, ds.SOPInstanceUID = meta.MediaStorageSOPClassUID, meta.MediaStorageSOPInstanceUID
    ds.Modality = "OT"
    ds.Rows, ds.Columns = 8, 10
    ds.SamplesPerPixel, ds.PhotometricInterpretation = 1, "MONOCHROME2"
    ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelRepresentation = bits, bits, bits - 1, 0
    pixels = np.arange(80, dtype=np.uint16).reshape(8, 10)
    ds.PixelData = pixels.astype("u1" if bits == 8 else "<u2").tobytes()
    for name, value in values.items():
        setattr(ds, name, value)
    ds.save_as(path, enforce_file_format=True)
    return pixels


class ReferenceTests(unittest.TestCase):
    def test_nested_ot_cr_and_extensionless_files_exclude_auxiliary_and_non_grayscale(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "标准片 Folder"
            write_reference(root / "p影组合片.DCM")
            write_reference(root / "子目录/标准片", bits=8, Modality="CR")
            write_reference(root / "__MACOSX/p影组合片.dcm")
            write_reference(root / "._辅助/坏文件.dcm")
            write_reference(root / "._p影组合片.dcm")
            write_reference(root / "视频.dcm", NumberOfFrames=2)
            write_reference(root / "彩色.dcm", PhotometricInterpretation="RGB", SamplesPerPixel=3)
            (root / "损坏.dcm").write_bytes(b"not a DICOM")
            images = scan_references(root)
            self.assertEqual({image["relative_path"] for image in images}, {"p影组合片.DCM", "子目录/标准片"})
            self.assertTrue((root / "__MACOSX/p影组合片.dcm").exists())
            with self.assertRaisesRegex(ValueError, "辅助目录"):
                scan_references(root / "__MACOSX")

    def test_stored_order_8_16_bit_range_rescale_inversion_and_pixel_aspect(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "片.dcm"
            for bits in (8, 16):
                pixels = write_reference(path, bits, PixelSpacing=[.2, .4])
                data = read_reference(path)
                np.testing.assert_array_equal(data["pixels"], pixels)
                self.assertEqual(data["spacing"], (.4, .2, 1))
                self.assertEqual((data["window"], data["level"], data["inverted"]), (79, 39.5, False))
            write_reference(path, PhotometricInterpretation="MONOCHROME1", RescaleSlope=2,
                            RescaleIntercept=-100, PixelAspectRatio=[2, 3])
            data = read_reference(path)
            np.testing.assert_array_equal(data["pixels"], pixels * 2.0 - 100)
            self.assertEqual((data["window"], data["level"], data["inverted"]), (158, -21, True))
            self.assertEqual(data["spacing"], (3, 2, 1))

    def test_dicom_voi_window_uses_declared_presentation_range_without_autostretching(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "窗位.dcm"
            write_reference(path, bits=8, WindowCenter=128, WindowWidth=256)
            data = read_reference(path)
            self.assertEqual((data["window"], data["level"]), (255, 127.5))
            self.assertEqual(float(data["pixels"].max()), 79)

    def test_missing_empty_and_unusable_directory_and_invalid_spacing_fail_clearly(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "不存在|存在"):
                scan_references(root / "missing")
            with self.assertRaisesRegex(ValueError, "没有找到"):
                scan_references(root)
            path = root / "无效间距.dcm"
            write_reference(path, PixelSpacing=[0, 1])
            with self.assertRaisesRegex(ValueError, "间距"):
                read_reference(path)
