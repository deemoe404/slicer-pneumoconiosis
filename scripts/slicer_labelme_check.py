"""Exercise the real export button/worker using an isolated synthetic chest image."""
import json
from pathlib import Path
import time
import traceback
import uuid

import numpy as np
import pydicom
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import DigitalXRayImageStorageForPresentation, ExplicitVRLittleEndian, generate_uid
import qt
import slicer
import vtk
from vtk.util.numpy_support import vtk_to_numpy

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "local-data/qa/labelme-ui" / uuid.uuid4().hex
REPORT = {"checks": []}


def check(condition, message):
    if not condition:
        raise AssertionError(message)
    REPORT["checks"].append(message)


def events():
    slicer.app.processEvents()
    time.sleep(.01)


def run():
    exit_code = 1
    try:
        source = OUT / "input"
        source.mkdir(parents=True)
        png = vtk.vtkPNGReader()
        png.SetFileName(str(ROOT / "docs/img/synthetic-chest.png"))
        png.Update()
        image = png.GetOutput()
        columns, rows, _ = image.GetDimensions()
        rgb = vtk_to_numpy(image.GetPointData().GetScalars()).reshape(rows, columns, image.GetNumberOfScalarComponents())
        gray = rgb[..., :3].mean(axis=-1) if rgb.shape[-1] >= 3 else rgb[..., 0]
        pixels = np.flipud(gray).astype(np.uint8)
        meta = FileMetaDataset()
        meta.TransferSyntaxUID = ExplicitVRLittleEndian
        meta.MediaStorageSOPClassUID = DigitalXRayImageStorageForPresentation
        meta.MediaStorageSOPInstanceUID = generate_uid()
        path = source / "合成胸片.dcm"
        ds = FileDataset(str(path), {}, file_meta=meta, preamble=b"\0" * 128)
        ds.SOPClassUID, ds.SOPInstanceUID = meta.MediaStorageSOPClassUID, meta.MediaStorageSOPInstanceUID
        ds.StudyInstanceUID, ds.SeriesInstanceUID = generate_uid(), generate_uid()
        ds.Modality, ds.Rows, ds.Columns = "DX", rows, columns
        ds.PatientName, ds.PatientID, ds.PatientOrientation = "SYNTHETIC^DEMO", "SYNTHETIC-DEMO", ["L", "F"]
        ds.SamplesPerPixel, ds.PhotometricInterpretation = 1, "MONOCHROME2"
        ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 8, 8, 7, 0
        ds.WindowCenter, ds.WindowWidth, ds.PixelData = 128, 256, pixels.tobytes()
        ds.save_as(path, enforce_file_format=True)
        slicer.util.selectModule("PneumoconiosisAnnotator")
        w = slicer.app.moduleManager().module("PneumoconiosisAnnotator").widgetRepresentation().self()
        check(not w.labelmeExport.button.isEnabled(), "LabelMe button is disabled before opening a case")
        w.dataRoot = OUT / "settings"
        check(w.openDirectory(source, OUT / "project", "reader-01"), "synthetic chest loads in the real module")
        from PneumoconiosisLib.core import empty_zones
        point = {"id": str(uuid.uuid4()), "kind": "point", "label": "测试定位点",
                 "points_ijk": [[columns * .3, rows * .4, 0]]}
        polygon = {"id": str(uuid.uuid4()), "kind": "polygon", "label": "测试区域",
                   "points_ijk": [[columns * x, rows * y, 0] for x, y in ((.6, .5), (.8, .5), (.8, .7), (.6, .7))]}
        case_id = w.cases[0]["case_id"]
        w.currentRecord = w.store.save(case_id, "reader-01", empty_zones(), spatial_annotations=[point, polygon])
        w._populate()
        w._restoreSpatial()
        w.features.restore()
        w.store.save(case_id, "reader-02", empty_zones(), spatial_annotations=[dict(point, label="第二位读者")])
        w.readerCombo.setCurrentIndex(w.readerCombo.findData("reader-03"))
        check(w.currentReader == "reader-03", "export can start from an unread reader")
        window = slicer.util.mainWindow()
        window.resize(1400, 950)
        window.setWindowTitle("LabelMe 导出检查 · 合成胸片")
        w.viewerToolbar.applyInterface()
        destination = OUT / "exports"
        destination.mkdir()
        timer = qt.QTimer()
        visits = []

        def choose_directory():
            for dialog in slicer.app.topLevelWidgets():
                if isinstance(dialog, qt.QFileDialog) and dialog.isVisible() and dialog.windowTitle == "选择 LabelMe 导出目录":
                    visits.append(True)
                    timer.stop()
                    dialog.setDirectory(str(destination))
                    dialog.accept()

        timer.connect("timeout()", choose_directory)
        timer.start(50)
        w.labelmeExport.button.click()
        timer.stop()
        check(bool(visits) and w.labelmeExport.worker is not None, "button opens a directory picker and starts the actual worker")
        check(not w.labelmeExport.button.isEnabled(), "repeated export is disabled while the worker runs")
        deadline = time.monotonic() + 60
        while w.labelmeExport.worker is not None and time.monotonic() < deadline:
            events()
        check(w.labelmeExport.worker is None and w.labelmeExport.last_result is not None,
              "background export completes: " + str(w.labelmeExport.last_error))
        result = w.labelmeExport.last_result
        bundle = Path(result["directory"])
        check(result["reader_count"] == 2 and not (bundle / "reader-03").exists(), "only the two participating readers are exported")
        first = json.loads((bundle / "reader-01" / (case_id + ".json")).read_text(encoding="utf-8"))
        second = json.loads((bundle / "reader-02" / (case_id + ".json")).read_text(encoding="utf-8"))
        check([s["shape_type"] for s in first["shapes"]] == ["point", "polygon"] and len(second["shapes"]) == 1,
              "each reader has independent point/polygon shapes")
        np.testing.assert_allclose(first["shapes"][0]["points"], [point["points_ijk"][0][:2]], atol=1e-6, rtol=0)
        check(True, "original pixel coordinates survive GUI export")
        check(w.labelmeExport.button.isEnabled() and w.labelmeExport.temporary is None, "button and temporary job files recover after export")
        w.readerCombo.setCurrentIndex(w.readerCombo.findData("reader-01"))
        w.fitImage()
        for _ in range(15):
            events()
        check(window.grab().save(str(OUT / "labelme-button.png")), "capture the new button in the actual module")
        REPORT.update(success=True, bundle=str(bundle))
        exit_code = 0
    except Exception:
        REPORT.update(success=False, error=traceback.format_exc())
    finally:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "report.json").write_text(json.dumps(REPORT, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"report": str(OUT / "report.json"), **REPORT}, ensure_ascii=True), flush=True)
        slicer.app.exit(exit_code)


run()
