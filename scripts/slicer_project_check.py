"""Real launcher/picker/quit checks. Run twice with PNEUMO_WORKFLOW_PHASE=create, then reopen."""
import json
import os
from pathlib import Path
import runpy
import shutil
import sqlite3
import subprocess
import sys
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
OUT = Path(os.environ["PNEUMO_WORKFLOW_OUT"])
PHASE = os.environ.get("PNEUMO_WORKFLOW_PHASE", "create")
OUT.mkdir(parents=True, exist_ok=True)
REPORT = {"phase": PHASE, "checks": [], "status": "running"}
source, project = OUT / "胸片", OUT / "标注项目"
report_file = OUT / (PHASE + "-report.json")


def check(value, message):
    if not value:
        raise AssertionError(message)
    REPORT["checks"].append(message)
    print("PASS: " + message, flush=True)


def events(seconds=.15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        slicer.app.processEvents()
        time.sleep(.01)


def picker(button, name, directory=None):
    if sys.platform == "darwin":
        sys.path.insert(0, str(ROOT / "scripts"))
        from macos_native_picker import drive_native_picker
        drive_native_picker(button.click, directory)
        check(True, "real macOS folder picker: " + name)
        return
    visited = []
    def select():
        for dialog in slicer.util.findChildren(slicer.util.mainWindow(), name=name):
            if dialog.isVisible():
                visited.append(name)
                if directory is None:
                    dialog.reject()
                else:
                    dialog.setDirectory(str(directory))
                    dialog.accept()
    timer = qt.QTimer()
    timer.connect("timeout()", select)
    timer.start(30)
    try:
        button.click()
    finally:
        timer.stop()
    check(visited, "real folder picker: " + name)


def fixture():
    source.mkdir()
    reader = vtk.vtkPNGReader()
    reader.SetFileName(str(ROOT / "docs/img/synthetic-chest.png"))
    reader.Update()
    image = reader.GetOutput()
    columns, rows, _ = image.GetDimensions()
    pixels = vtk_to_numpy(image.GetPointData().GetScalars()).reshape(rows, columns, -1)
    gray = np.flipud(pixels[..., :3].mean(axis=-1)).astype(np.uint16) * 16
    for index in range(2):
        meta = FileMetaDataset()
        meta.MediaStorageSOPClassUID = DigitalXRayImageStorageForPresentation
        meta.MediaStorageSOPInstanceUID = generate_uid()
        meta.TransferSyntaxUID = ExplicitVRLittleEndian
        ds = FileDataset(str(source / f"合成-{index}.dcm"), {}, file_meta=meta, preamble=b"\0" * 128)
        ds.SOPClassUID, ds.SOPInstanceUID = meta.MediaStorageSOPClassUID, meta.MediaStorageSOPInstanceUID
        ds.StudyInstanceUID, ds.SeriesInstanceUID = generate_uid(), generate_uid()
        ds.Modality, ds.PatientName, ds.PatientID = "DX", "SYNTHETIC^DEMO", "SYNTHETIC-DEMO"
        ds.PatientOrientation = ["L", "F"]
        ds.Rows, ds.Columns, ds.SamplesPerPixel = rows, columns, 1
        ds.PhotometricInterpretation = "MONOCHROME2"
        ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 16, 12, 11, 0
        ds.WindowCenter, ds.WindowWidth = 2048, 4096
        ds.PixelData = gray.astype("<u2").tobytes()
        ds.save_as(ds.filename, enforce_file_format=True)


def write_report():
    report_file.write_text(json.dumps(REPORT, ensure_ascii=False, indent=2), encoding="utf-8")


def on_quit():
    if REPORT["status"] != "awaiting-clean-exit":
        write_report()
        return
    try:
        with sqlite3.connect(project / "annotations.sqlite3") as connection:
            payload = connection.execute("SELECT payload_json FROM annotations WHERE reader_id='reader-01' AND case_id=?",
                                         (REPORT["case_id"],)).fetchone()[0]
        check(json.loads(payload)["quality"]["grade"] == 3, "actual application exit persisted the last unsaved input")
        check(not REPORT.get("dialogs"), "application exits without any native scene-save prompt")
        REPORT["status"] = "passed"
    except Exception:
        REPORT["status"], REPORT["error"] = "failed", traceback.format_exc()
    write_report()


def watch_dialogs():
    for widget in slicer.app.topLevelWidgets():
        if widget.isVisible() and widget.inherits("QMessageBox"):
            REPORT.setdefault("dialogs", []).append(str(widget.text))
            widget.reject()


def run():
    try:
        if os.environ.get("PNEUMO_AX_PROBE"):
            subprocess.Popen([os.environ["PNEUMO_AX_PROBE"], str(os.getpid()), str(OUT / (PHASE + "-ax.json"))])
        if PHASE == "create":
            fixture()
            project.mkdir()
        profile = Path(os.environ["PNEUMO_DATA_ROOT"])
        profile.mkdir(parents=True, exist_ok=True)
        (profile / "last-project.json").write_text(json.dumps({"source": str(source), "project": str(project)}))
        (profile / "reference-library.json").write_text(json.dumps({"directory": str(source), "visible": True}))
        startup = runpy.run_path(str(ROOT / "scripts/start.py"))
        startup["start"]()
        w = slicer.app.moduleManager().module("PneumoconiosisAnnotator").widgetRepresentation().self()
        window = slicer.util.mainWindow()
        events()
        check(w.store is None and w.sourceDirectory is None and w.volume is None and w.references.volume is None,
              "startup ignores remembered project, sample images and reference library")
        check(w.saveLabel.isVisible() and not w.exportButton.isEnabled() and not w.newProjectButton.isEnabled(),
              "empty state explains the next step and disables editing/export")
        if PHASE == "create":
            window.grab().save(str(OUT / "empty.png"))
        picker(w.openButton, "PneumoCaseDirectoryDialog", source)
        check(w.store is None and w.volume is None and len(w.cases) == 2 and w.newProjectButton.isEnabled(),
              "source selection scans images without creating a project or loading a case")
        picker(w.newProjectButton, "PneumoNewProjectDialog")
        check(w.store is None and not (project / "annotations.sqlite3").exists() if PHASE == "create" else w.store is None,
              "cancelling project selection leaves the source-only state")
        picker(w.newProjectButton if PHASE == "create" else w.openProjectButton,
               "PneumoNewProjectDialog" if PHASE == "create" else "PneumoOpenProjectDialog", project)
        check(w.currentRecord is not None and w.store.directory == project.resolve(), "explicit project loads the first or last-read image")
        check(w.currentIndex == 0, "new projects start at the first image; reopening restores that nonfinal image")
        check(str(source) in w.sourceLabel.text and str(project) in w.projectLabel.text and w.saveLabel.isVisible(),
              "current source, project and save state stay visible")
        REPORT["case_id"] = w.currentRecord["case_id"]
        if PHASE == "create":
            w.features.qualityChoices.buttons[2].click()
            check(w.dirty and "正在保存" in w.saveLabel.text, "editing shows a pending save")
            events(.6)
            check(not w.dirty and "已自动保存" in w.saveLabel.text, "autosave visibly finishes")
            case = w.cases[w.currentIndex]
            shape = {"id": str(uuid.uuid4()), "kind": "circle", "label": "P1",
                     "points_ijk": [[case["columns"] * .3, case["rows"] * .4, 0],
                                    [case["columns"] * .36, case["rows"] * .4, 0]]}
            w.markups.restore(w.volume, [shape], case)
            w.spatialChanged(w.markups.capture())
            check(w.flushSave(), "circle saves into the explicit project")
            # Simulate a real disk-write failure before clicking the actual close button.
            w.store.connection.execute("CREATE TRIGGER reject_save BEFORE INSERT ON annotations BEGIN SELECT RAISE(ABORT, 'test disk failure'); END")
            w.features.qualityChoices.buttons[3].click()
            window.close()
            events(.3)
            check(window.isVisible() and w.store is not None and w.dirty and w.saveError and w.retryButton.isVisible(),
                  "a failed save blocks real window close and preserves input")
            window.grab().save(str(OUT / "save-failure.png"))
            w.store.connection.execute("DROP TRIGGER reject_save")
            w.retryButton.click()
            check(not w.dirty and not w.saveError and not w.retryButton.isVisible(), "retry saves the same buffered input")
        else:
            check(w.currentRecord["quality"]["grade"] == 3 and len(w.spatialState) == 1,
                  "a fresh process restores saved quality and circle")
            w.readerCombo.setCurrentIndex(w.readerCombo.findData("reader-02"))
            check(w.currentRecord["quality"]["grade"] is None, "reader records remain independent")
            w.features.qualityChoices.buttons[1].click()
            w.flushSave()
            bundle = w.exportResults(OUT / "交付")
            manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
            check(len(manifest["readers"]) == 2 and not manifest["includes_source_images"] and not list(bundle.rglob("*.dcm")),
                  "delivery includes every participating reader, with original images excluded")
            check(all(r["status_counts"] == {"complete": 0, "draft": 1, "unread": 1} for r in manifest["readers"]),
                  "delivery explicitly counts drafts and unread cases")
            check(w.store.directory == project.resolve(), "export leaves the working project unchanged")
            wrong = OUT / "另一批胸片"
            wrong.mkdir()
            shutil.copy2(next(source.glob("*.dcm")), wrong / "only-one.dcm")
            try:
                w.openDirectory(wrong, project, create=False)
            except ValueError as exc:
                check("不匹配" in str(exc), "wrong image batch is rejected")
            else:
                raise AssertionError("wrong image batch was accepted")
            check(w.store.directory == project.resolve() and w.volume is not None and w.sourceDirectory == source.resolve(),
                  "failed project opening retains the active project and image")
            w.readerCombo.setCurrentIndex(w.readerCombo.findData("reader-01"))
        w.references.set_visible(False)
        window.resize(1450, 1050)
        slicer.util.findChild(window, "PanelDockWidget").setMinimumWidth(470)
        w.fitImage()
        events()
        window.grab().save(str(OUT / (PHASE + "-active.png")))
        # Leave a pending change for the close guard, rather than pre-saving it.
        w.features.qualityChoices.buttons[2].click()
        w.features.qualityChoices.buttons[3].click()
        REPORT["status"] = "awaiting-clean-exit"
        if os.environ.get("PNEUMO_AX_PROBE"):
            probe = json.loads((OUT / (PHASE + "-ax.json")).read_text())
            check(probe["trusted"] and probe["successfulQueries"] > 100,
                  "external macOS accessibility selection queries remain functional during native picker use")
        write_report()
        window.close()
    except Exception:
        REPORT["status"], REPORT["error"] = "failed", traceback.format_exc()
        print(REPORT["error"], flush=True)
        write_report()
        slicer.app.exit(1)


slicer.app.connect("aboutToQuit()", on_quit)
dialog_watch = qt.QTimer()
dialog_watch.connect("timeout()", watch_dialogs)
dialog_watch.start(40)
qt.QTimer.singleShot(500, run)
