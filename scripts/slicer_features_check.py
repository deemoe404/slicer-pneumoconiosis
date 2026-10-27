"""Real DICOM/model/Qt/Markups/Segment Editor/audio integration, in a private QA store."""
import csv
import json
from pathlib import Path
import time
import traceback
import uuid

import numpy as np
import qt
import slicer
import vtk

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "local-data" / "qa"
REPORT = {"checks": []}


def check(condition, message):
    if not condition:
        raise AssertionError(message)
    REPORT["checks"].append(message)
    print(message, flush=True)


def events(seconds=.15):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        slicer.app.processEvents()
        time.sleep(.01)


def wait_for(condition, seconds=45):
    end = time.monotonic() + seconds
    while not condition() and time.monotonic() < end:
        events(.05)
    if not condition():
        raise AssertionError("Timed out waiting for the native feature: " + w.saveLabel.text)


def xy(point):
    matrix = vtk.vtkMatrix4x4()
    w.volume.GetIJKToRASMatrix(matrix)
    world = matrix.MultiplyPoint(list(point) + [1])
    inverse = vtk.vtkMatrix4x4()
    inverse.DeepCopy(slicer.app.layoutManager().sliceWidget("Red").mrmlSliceNode().GetXYToRAS())
    inverse.Invert()
    return tuple(round(v) for v in inverse.MultiplyPoint(world)[:2])


def mouse(point, end=None):
    view = slicer.app.layoutManager().sliceWidget("Red").sliceView()
    interactor = view.interactorStyle().GetInteractor()
    start = xy(point)
    interactor.EnterEvent()
    interactor.SetEventPosition(*start)
    interactor.MouseMoveEvent()
    slicer.util.clickAndDrag(view, "Left", start, xy(end) if end else start, steps=12 if end else 1)
    events(.1)


try:
    slicer.util.selectModule("PneumoconiosisAnnotator")
    w = slicer.app.moduleManager().module("PneumoconiosisAnnotator").widgetRepresentation().self()
    store = OUT / "features-store" / uuid.uuid4().hex
    REPORT["project_directory"] = str(store)
    w.dataRoot = OUT / "features-settings"
    check(w.openDirectory(ROOT.parent / "2023Sample", store, "reader-01"), "open 11 existing DICOM samples with the new feature controls")
    f = w.features
    window = slicer.util.mainWindow()
    window.setWindowTitle("QA 仅功能测试 · 尘肺胸片标注")
    window.resize(1450, 1000)
    slicer.util.setToolbarsVisible(False)
    slicer.util.setDataProbeVisible(False)
    slicer.util.setModulePanelTitleVisible(False)
    events(.3)
    w.fitImage()
    events()
    from PneumoconiosisLib.core import file_sha256
    from PneumoconiosisLib.partition import zone_map
    first = w.cases[0]["case_id"]
    check(f.qualityChoices.value is None, "whole-film quality starts unread without a fabricated grade")
    f.qualityChoices.buttons[3].click()
    events(.65)
    check(w.store.get(first, "reader-01")["quality"] == {"grade": 3, "reason": ""}, "quality radio buttons automatically save a poor-quality grade without requiring a defect reason")

    f.recordButton.click()
    wait_for(lambda: f.recording_started is not None or f.last_audio_error is not None, 15)
    check(f.recording_started is not None, "native Slicer control starts the real microphone worker")
    events(1.1)
    check(w.selectCase(1), "case switch stops and saves an ongoing recording before leaving")
    audio = w.store.get(first, "reader-01")["audio"]
    check(len(audio) == 1 and audio[0]["frames"] > 0, "actual microphone PCM is attached to the original case and reader")
    check(not f.audio, "next case does not inherit the previous recording")
    check(w.selectCase(0), "switch back restores the original audio and quality")
    f.playButton.click()
    wait_for(lambda: f.player is not None and f.player.process.state() == qt.QProcess.NotRunning, 15)
    check(f.player.process.exitCode() == 0, "real audio output worker plays the saved WAV successfully")

    f.autoButton.click()
    wait_for(lambda: not f.jobs)
    check(f.partition.state is not None, "real pretrained lung model result is applied to native Slicer segmentation nodes")
    check(f.partition.state["state"] == "pending_review", "machine-generated six-zone geometry requires clinician confirmation")
    check(f.partition.overlay.GetSegmentation().GetNumberOfSegments() == 6, "six native overlay segments exist on the original radiograph")
    check(all(v["state"] == "unread" for v in w._zonesFromUI().values()), "automatic anatomical partition never fills clinical profusion ratings")
    before = f.partition.capture()
    before_masks = f.partition.masks()
    before_map = zone_map(*before_masks, before["landmarks"])
    f.editPointsButton.click()
    anchor = before["landmarks"]["R"]["dome"]
    mouse(anchor, [anchor[0], anchor[1] - 60, 0])
    events(.8)
    check(w.flushSave(), "manual landmark edit autosaves")
    edited = w.currentRecord["partition"]
    check(abs(edited["landmarks"]["R"]["dome"][1] - (anchor[1] - 60)) < 5,
          "native mouse drag corrects the diaphragm dome in original image coordinates")
    check(edited["baseline_landmarks"] == before["baseline_landmarks"] and edited["state"] == "edited", "manual boundary changes retain the original machine landmarks and require review again")
    after_map = zone_map(*f.partition.masks(), edited["landmarks"])
    check(np.any(after_map != before_map), "dragging the dome recomputes the six-zone partition")
    f.editPointsButton.setChecked(False)

    f.editLungsButton.click()
    check(f.partition.editor is not None, "native Segment Editor opens for manual lung-mask correction")
    editor = f.partition.editor
    editor.setCurrentSegmentID("R")
    editor.setActiveEffectByName("Erase")
    effect = editor.activeEffect()
    effect.setCommonParameter("BrushSphere", "0")
    effect.setCommonParameter("BrushRelativeDiameter", "2")
    mask = f.partition.masks()[0]
    yy, xx = np.nonzero(mask)
    row = int(np.median(yy))
    columns = np.flatnonzero(mask[row])
    column = int(np.median(columns))
    count = int(mask.sum())
    mouse([column, row, 0])
    events(.7)
    check(int(f.partition.masks()[0].sum()) < count, "real native eraser interaction changes the selected lung mask")
    f.partition.close_editor()
    check(w.flushSave(), "painted lung-mask corrections are automatically stored as immutable assets")
    edited = w.currentRecord["partition"]
    check(edited["masks"] != edited["baseline"], "corrected lung masks do not overwrite the retained machine result")
    f.confirmButton.click()
    check(w.currentRecord["partition"]["state"] == "confirmed", "clinician confirmation persists after correcting masks and landmarks")
    expected_first = json.loads(json.dumps(w.currentRecord))
    check(w.selectCase(1) and w.selectCase(0), "case navigation restores corrected partition geometry")
    check(w.currentRecord["partition"] == expected_first["partition"], "case switch restores exact mask hashes, landmarks and confirmation state")

    w.readerCombo.setCurrentIndex(w.readerCombo.findData("reader-02"))
    check(w.changeReader(), "switch reader with independent feature records")
    check(f.qualityChoices.value is None and not f.audio, "quality and audio remain independent for another reader")
    check(f.partition.state["state"] == "pending_review" and f.partition.state["masks"] == before["baseline"], "another reader sees the machine baseline and no other clinician's corrections or confirmation")
    w.readerCombo.setCurrentIndex(w.readerCombo.findData("reader-01"))
    check(w.changeReader() and w.currentRecord["partition"]["state"] == "confirmed", "returning reader restores their own confirmed correction")

    w.selectCase(1)
    check(f.segment(), "start background inference for a different case")
    check(w.selectCase(2), "navigation remains possible while the model runs")
    wait_for(lambda: not f.jobs)
    check(w.currentIndex == 2 and f.partition.state is None, "late model result cannot attach to the next case")
    check(w.selectCase(1) and f.partition.state is not None, "late result is cached and recovered for its actual source case")

    for index in range(len(w.cases)):
        check(w.selectCase(index), f"load sample {index + 1:02d} for six-zone verification")
        if f.partition.state is None:
            check(f.segment(), f"launch pretrained segmentation for sample {index + 1:02d}")
            wait_for(lambda: not f.jobs)
        check(f.partition.state is not None, f"sample {index + 1:02d} has a real model-generated partition")
        labels = zone_map(*f.partition.masks(), f.partition.landmarks())
        check(labels.shape == (w.cases[index]["rows"], w.cases[index]["columns"]) and set(np.unique(labels)) == set(range(7)),
              f"sample {index + 1:02d} has six nonempty zones aligned to original pixels")
    check(all(file_sha256(c["file_path"]) == c["source_sha256"] for c in w.cases), "all 11 original DICOM files remain unchanged")
    w.selectCase(0)
    w.store.connection.executescript("CREATE TEMP TRIGGER reject_feature BEFORE INSERT ON annotations BEGIN SELECT RAISE(ABORT, 'QA feature write failure'); END;")
    f.qualityChoices.buttons[4].click()
    check(not w.selectCase(1) and w.currentIndex == 0, "attachment/quality write failure blocks navigation and retains the current native geometry")
    close = qt.QCloseEvent()
    check(w.closeGuard.eventFilter(window, close) and not close.isAccepted(), "feature save failure blocks normal application close")
    w.store.connection.execute("DROP TRIGGER reject_feature")
    check(w.flushSave(), "retry saves quality and attachments without losing edits")
    paths = w.exportResults(OUT / "features-exports")
    package = json.loads(paths[1].read_text())
    rows = list(csv.DictReader(paths[0].read_text(encoding="utf-8-sig").splitlines()))
    check(len(rows) == 66 and len(package["records"]) == 11, "export bundle includes 11 cases, 66 ratings and whole-film quality fields")
    check((paths[1].parent / package["records"][0]["audio"][0]["path"]).exists(), "export bundle contains actual microphone WAV data")
    check(all((paths[1].parent / r["partition"]["zone_map"]["path"]).exists() for r in package["records"]), "export bundle contains original-resolution six-zone maps for all 11 cases")
    REPORT["expected_first"] = w.currentRecord
    REPORT["export_json"] = str(paths[1])
    f.partition.set_visible(True)
    events(.2)
    check(window.grab().save(str(OUT / "features-preview.png")), "capture the actual new Slicer quality, recording and editable partition interface")
    REPORT["success"] = True
except Exception:
    REPORT["success"] = False
    REPORT["error"] = traceback.format_exc()
    traceback.print_exc()
    if "w" in globals():
        REPORT["save_status"] = w.saveLabel.text
        REPORT["feature_status"] = w.features.partitionLabel.text
        REPORT["worker_error"] = w.features.model_worker.stderr if w.features.model_worker else None
        slicer.util.mainWindow().grab().save(str(OUT / "features-failure.png"))
(OUT / "features-result.json").write_text(json.dumps(REPORT, ensure_ascii=False, indent=2))
slicer.util.exit(0 if REPORT["success"] else 1)
