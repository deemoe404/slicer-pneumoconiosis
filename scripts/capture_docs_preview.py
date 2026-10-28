"""Capture the real module with a synthetic fixture; run in an isolated Slicer process.

No patient images, existing projects or microphone input are used. The prepared
lung model runs on the ImageGen fixture only; this does not validate its accuracy.
Only docs/img/main.png is a delivery; fixture data stays under ignored local-data/qa.
"""
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


def events(seconds=.2):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        slicer.app.processEvents()
        time.sleep(.01)


def capture():
    fixture = ROOT / "local-data" / "qa" / "docs-preview" / uuid.uuid4().hex
    source = fixture / "input"
    source.mkdir(parents=True)
    reader = vtk.vtkPNGReader()
    reader.SetFileName(str(ROOT / "docs" / "img" / "synthetic-chest.png"))
    reader.Update()
    image = reader.GetOutput()
    columns, rows, _ = image.GetDimensions()
    channels = image.GetNumberOfScalarComponents()
    rgb = vtk_to_numpy(image.GetPointData().GetScalars()).reshape(rows, columns, channels)
    # PNG's VTK origin is bottom-left; DICOM stores its first row at the top.
    gray = rgb[..., :3].mean(axis=-1) if channels >= 3 else rgb[..., 0]
    pixels = np.flipud(gray).astype(np.uint16) * 16

    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = DigitalXRayImageStorageForPresentation
    meta.MediaStorageSOPInstanceUID = generate_uid()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds = FileDataset(str(source / "synthetic.dcm"), {}, file_meta=meta, preamble=b"\0" * 128)
    ds.SOPClassUID = meta.MediaStorageSOPClassUID
    ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
    ds.StudyInstanceUID, ds.SeriesInstanceUID = generate_uid(), generate_uid()
    ds.Modality = "DX"
    ds.PatientName, ds.PatientID = "SYNTHETIC^DEMO", "SYNTHETIC-DEMO"
    ds.PatientOrientation = ["L", "F"]
    ds.SeriesDescription = "Synthetic documentation fixture - no patient data"
    ds.Rows, ds.Columns, ds.SamplesPerPixel = rows, columns, 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 16, 12, 11, 0
    ds.WindowCenter, ds.WindowWidth = 2048, 4096
    ds.PixelData = pixels.astype("<u2").tobytes()
    ds.save_as(ds.filename, enforce_file_format=True)

    slicer.util.selectModule("PneumoconiosisAnnotator")
    widget = slicer.app.moduleManager().module("PneumoconiosisAnnotator").widgetRepresentation().self()
    widget.dataRoot = fixture / "settings"
    if not widget.openDirectory(source, fixture / "project", "reader-01"):
        raise RuntimeError("Synthetic documentation fixture could not be opened")

    from PneumoconiosisLib.core import ZONES
    if not widget.features.segment():
        raise RuntimeError("Demo lung segmentation could not start")
    deadline = time.monotonic() + 60
    while widget.features.jobs and time.monotonic() < deadline:
        events(.1)
    if widget.features.jobs or widget.features.partition.state is None:
        raise RuntimeError("Demo lung segmentation failed or timed out: " + widget.saveLabel.text)
    partition = widget.features.partition.capture()
    annotations = [
        {"id": str(uuid.uuid4()), "kind": "circle", "label": "P1",
         "points_ijk": [[columns * .3, rows * .4, 0], [columns * .36, rows * .44, 0]]},
        {"id": str(uuid.uuid4()), "kind": "polygon", "label": "R1",
         "points_ijk": [[columns * i, rows * j, 0] for i, j in ((.60, .52), (.75, .53), (.74, .66), (.59, .65))]},
    ]
    zones = {zone: {"state": "scored", "grade": "0/0"} for zone in ZONES}
    widget.currentRecord = widget.store.save(
        widget.cases[0]["case_id"], widget.currentReader, zones,
        quality={"grade": 2, "reason": ""}, partition=partition, spatial_annotations=annotations)
    widget._populate()
    widget._restoreSpatial()
    widget.features.restore()
    widget.updateControls()

    window = slicer.util.mainWindow()
    window.setWindowTitle("尘肺胸片标注 · 合成演示")
    window.resize(1450, 1050)
    slicer.util.setPythonConsoleVisible(False)
    slicer.util.findChild(window, "PanelDockWidget").setMinimumWidth(520)
    events(.5)
    widget.fitImage()
    view = slicer.app.layoutManager().sliceWidget("Red").sliceView()
    watermark = qt.QLabel("SYNTHETIC DEMO · 合成演示，非患者影像", view)
    watermark.setStyleSheet("color:#ffffff; background:#24394f; padding:8px 12px; font-size:16px;")
    watermark.adjustSize()
    watermark.move(20, 20)
    watermark.show()
    events(.4)
    destination = ROOT / "docs" / "img" / "main.png"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not window.grab().save(str(destination)):
        raise RuntimeError("Documentation screenshot could not be saved")
    print("Synthetic documentation screenshot: " + str(destination), flush=True)
    widget.cleanup()


try:
    capture()
except Exception:
    traceback.print_exc()
    slicer.util.exit(1)
else:
    slicer.util.exit(0)
