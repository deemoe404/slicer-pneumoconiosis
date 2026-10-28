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


def mouse(widget, point, end=None):
    matrix = vtk.vtkMatrix4x4()
    widget.volume.GetIJKToRASMatrix(matrix)
    inverse = vtk.vtkMatrix4x4()
    inverse.DeepCopy(slicer.app.layoutManager().sliceWidget("Red").mrmlSliceNode().GetXYToRAS())
    inverse.Invert()
    def xy(position):
        return tuple(round(v) for v in inverse.MultiplyPoint(matrix.MultiplyPoint(position + [1]))[:2])
    view = slicer.app.layoutManager().sliceWidget("Red").sliceView()
    interactor = view.interactorStyle().GetInteractor()
    interactor.EnterEvent()
    interactor.SetEventPosition(*xy(point))
    interactor.MouseMoveEvent()
    slicer.util.clickAndDrag(view, "Left", xy(point), xy(end) if end else xy(point), steps=12 if end else 1)
    events()


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
        ds.PixelSpacing = [.2, .3]
        ds.save_as(path, enforce_file_format=True)
        ds.SOPInstanceUID = ds.file_meta.MediaStorageSOPInstanceUID = generate_uid()
        ds.save_as(source / "另一张合成胸片.dcm", enforce_file_format=True)
        slicer.util.selectModule("PneumoconiosisAnnotator")
        w = slicer.app.moduleManager().module("PneumoconiosisAnnotator").widgetRepresentation().self()
        check(not w.labelmeExport.button.isEnabled(), "LabelMe button is disabled before opening a case")
        w.dataRoot = OUT / "settings"
        check(w.openDirectory(source, OUT / "project", "reader-01"), "synthetic chest loads in the real module")
        from PneumoconiosisLib.core import empty_zones
        window = slicer.util.mainWindow()
        window.resize(1400, 1000)
        w.viewerToolbar.applyInterface()
        for _ in range(20):
            events()
        w.fitImage()
        events()
        w.ellipseButton.click()
        mouse(w, [columns * .3, rows * .4, 0])
        check(w.markups.placing_id is not None, "first corner keeps ellipse placement active")
        mouse(w, [columns * .36, rows * .44, 0])
        check(w.markups.placing_id is None and w.flushSave(), "two native clicks complete and save an ellipse")
        actual = w.currentRecord["spatial_annotations"][0]
        check(actual["kind"] == "ellipse" and actual["measurement"]["unit"] == "mm", "ellipse saves physical long and short diameters")
        node = w.markups.outlines[actual["id"]]
        check(node.GetNumberOfDefinedControlPoints() == 128, "ellipse has a curved outline with hidden generated vertices")
        before = actual["measurement"]["long_diameter"]
        mouse(w, actual["points_ijk"][1], [columns * .39, rows * .46, 0])
        check(w.flushSave() and w.currentRecord["spatial_annotations"][0]["measurement"]["long_diameter"] > before,
              "dragging an ellipse corner updates its size and persistence")
        check("mm" in w.markups.labels[actual["id"]].text and w.markups.labels[actual["id"]].isVisible(),
              "size label is visible beside the image annotation")
        loader = w._loadVolume
        def inspect_loading(case):
            check(w.busy.dialog is not None and w.busy.dialog.isVisible() and
                  slicer.app.activeModalWidget() == w.busy.dialog, "case loading covers the application with a modal status overlay")
            check(w.busy.dialog.width >= window.width and w.busy.dialog.height >= window.height,
                  "busy overlay covers the entire application window")
            canvas = window.grab()
            painter = qt.QPainter(canvas)
            painter.drawPixmap(0, 0, w.busy.dialog.grab())
            painter.end()
            canvas.save(str(OUT / "busy-overlay.png"))
            return loader(case)
        w._loadVolume = inspect_loading
        w.nextButton.click()
        check(w.currentIndex == 1 and w.busy.dialog is None, "next case unlocks the interface after loading")
        w._loadVolume = loader
        w.previousButton.click()
        check(w.currentIndex == 0 and len(w.markups.outlines) == 1, "previous case restores ellipse and its measured label")
        def fail_loading(case):
            raise RuntimeError("synthetic load failure")
        w._loadVolume = fail_loading
        check(not w.selectCase(1) and w.currentIndex == 0 and w.busy.dialog is None,
              "failed loading restores input access and retains the previous case")
        w._loadVolume = loader
        check(w.flushSave(), "annotation remains saveable after a loading failure")
        point = {"id": str(uuid.uuid4()), "kind": "ellipse", "label": "E1",
                 "points_ijk": [[columns * .3, rows * .4, 0], [columns * .36, rows * .44, 0]]}
        polygon = {"id": str(uuid.uuid4()), "kind": "polygon", "label": "R1",
                   "points_ijk": [[columns * x, rows * y, 0] for x, y in ((.6, .5), (.8, .5), (.8, .7), (.6, .7))]}
        case_id = w.cases[0]["case_id"]
        w.currentRecord = w.store.save(case_id, "reader-01", empty_zones(), spatial_annotations=[point, polygon])
        w._populate()
        w._restoreSpatial()
        w.features.restore()
        width_before = w.currentRecord["spatial_annotations"][1]["measurement"]["width"]
        for corner in (2, 3):
            point_before = w.markups.capture()[1]["points_ijk"][corner]
            mouse(w, point_before, [point_before[0], rows * .65, 0])
        check(w.flushSave() and w.currentRecord["spatial_annotations"][1]["measurement"]["width"] < width_before,
              "dragging polygon vertices recalculates and saves estimated width")
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
        check([s["shape_type"] for s in first["shapes"]] == ["polygon", "polygon"] and len(second["shapes"]) == 1,
              "each reader has independent ellipse/polygon shapes")
        np.testing.assert_allclose(first["shapes"][0]["source_points_ijk"], point["points_ijk"], atol=1e-6, rtol=0)
        check(True, "original pixel coordinates survive GUI export")
        check(w.labelmeExport.button.isEnabled() and w.labelmeExport.temporary is None, "button and temporary job files recover after export")
        w.labelmeExport.start(OUT / "canceled-export")
        w.labelmeExport.cancel()
        check(w.busy.dialog is None and w.labelmeExport.worker is None and w.labelmeExport.button.isEnabled(),
              "canceling export closes its full-window overlay and restores controls")
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
