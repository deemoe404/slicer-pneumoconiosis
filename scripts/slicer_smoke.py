"""Exercise actual Slicer widgets, source images, autosave and failure recovery in an isolated project."""
import csv
import json
import sys
import time
import traceback
import uuid
from pathlib import Path

import qt
import slicer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "PneumoconiosisAnnotator"))
from PneumoconiosisLib.core import AnnotationStore, GRADES, ZONES, empty_zones, file_sha256

OUT = ROOT / "local-data" / "qa"
OUT.mkdir(parents=True, exist_ok=True)
REPORT = {"checks": [], "slicer": slicer.app.applicationVersion}


def check(condition, message):
    if not condition:
        raise AssertionError(message)
    REPORT["checks"].append(message)


def events(seconds=0.1):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        slicer.app.processEvents()
        time.sleep(0.01)


def mouse(slider, event_type, step):
    handle = slider.style().pixelMetric(qt.QStyle.PM_SliderLength)
    point = qt.QPoint(round(handle / 2 + (slider.width - handle) * step / 11), slider.height // 2)
    held = qt.Qt.NoButton if event_type == qt.QEvent.MouseButtonRelease else qt.Qt.LeftButton
    button = qt.Qt.NoButton if event_type == qt.QEvent.MouseMove else qt.Qt.LeftButton
    event = qt.QMouseEvent(event_type, qt.QPointF(point), qt.QPointF(slider.mapToGlobal(point)), button, held, qt.Qt.NoModifier)
    slicer.app.sendEvent(slider, event)


def rate(widget, zone, grade):
    slider = widget.gradeSliders[zone].slider
    slider.setSliderPosition(GRADES.index(grade))
    slider.triggerAction(qt.QAbstractSlider.SliderMove)


def run():
    try:
        slicer.util.selectModule("PneumoconiosisAnnotator")
        REPORT["module_loaded"] = bool(slicer.app.moduleManager().module("PneumoconiosisAnnotator"))
        w = slicer.app.moduleManager().module("PneumoconiosisAnnotator").widgetRepresentation().self()
        qa_store = OUT / "integration-store" / uuid.uuid4().hex
        REPORT["project_directory"] = str(qa_store)
        w.dataRoot = OUT / "settings"
        check(all(not control.slider.isEnabled() for control in w.gradeSliders.values()),
              "rating sliders stay disabled until a case is loaded")
        bar = w.viewerToolbar
        check(all(not button.isEnabled() for button in (bar.windowLevelButton, bar.transformButton, bar.fitButton, bar.resetButton))
              and bar.fullButton.isEnabled(), "image controls require a case while the full-interface switch remains available")
        check(bar.windowLevelLabel.text == "窗宽 —  窗位 —", "window-level readout is empty before loading a radiograph")
        check(w.openDirectory(ROOT.parent / "2023Sample", qa_store, "reader-01"), "open current DICOM samples")
        check(len(w.cases) == 11, "11 eligible single-frame DX cases")
        hashes = {c["case_id"]: c["source_sha256"] for c in w.cases}
        check(w._zonesFromUI() == empty_zones(), "initial ratings are unread, never auto-filled")
        check(w.features.qualityWidget.property("completionState") == "missing"
              and w.ratingsWidget.property("completionState") == "missing"
              and w.completeButton.property("completionState") == "missing",
              "unread quality, six-zone ratings and completion action start red")
        check(all(group.property("completionState") == "ready" for group in
                  (w.features.partitionWidget, w.spatialGroup, w.features.audioWidget)),
              "empty optional partition, geometry and audio stay green without becoming required")
        check(all(control.slider.minimum == 0 and control.slider.maximum == 11 and control.slider.singleStep == 1
                  for control in w.gradeSliders.values()), "each lung zone has exactly twelve discrete slider grades")
        check(w.features.qualityChoices.value is None and len(w.features.qualityChoices.buttons) == 5,
              "quality exposes all four grades and unread as radio choices")
        events(.2)
        check([label.text for label in w.gradeRuler.labels] == list(GRADES)
              and all(a.geometry.right() < b.geometry.left() for a, b in zip(w.gradeRuler.labels, w.gradeRuler.labels[1:])),
              "the shared ruler displays all twelve small-grade labels without overlap")
        viewport = slicer.app.layoutManager().viewport()
        check(bar.frame.isVisible() and bar.frame.mapToGlobal(qt.QPoint(0, 0)).x() == viewport.mapToGlobal(qt.QPoint(0, 0)).x()
              and bar.frame.mapToGlobal(qt.QPoint(0, bar.frame.height)).y() <= viewport.mapToGlobal(qt.QPoint(0, 0)).y(),
              "the compact toolbar sits above and aligns with the image viewport")
        display = w.volume.GetDisplayNode()
        original_grey = (display.GetWindow(), display.GetLevel(), display.GetColorNodeID())
        check(bar.windowLevelLabel.text == f"窗宽 {original_grey[0]:.1f}  窗位 {original_grey[1]:.1f}",
              "the toolbar reads the loaded image's actual Slicer window and level")
        bar.windowLevelButton.click()
        check(w.markups.interaction.GetCurrentInteractionMode() == slicer.vtkMRMLInteractionNode.AdjustWindowLevel
              and bar.windowLevelButton.isChecked() and not bar.transformButton.isChecked(), "window-level button selects the native Slicer mouse mode")
        red = slicer.app.layoutManager().sliceWidget("Red")
        view = red.sliceView()
        centre = (view.width // 2, view.height // 2)
        slicer.util.clickAndDrag(view, "Left", centre, (centre[0] + 60, centre[1] + 40), steps=12)
        check((display.GetWindow(), display.GetLevel()) != original_grey[:2], "native left-button dragging changes image window and level")
        check(bar.windowLevelLabel.text == f"窗宽 {display.GetWindow():.1f}  窗位 {display.GetLevel():.1f}",
              "native mouse dragging updates the numeric readout immediately")
        display.SetWindowLevel(1234.5, 678.9)
        check(bar.windowLevelLabel.text == "窗宽 1234.5  窗位 678.9", "changes made through the native display node also synchronize without polling")
        bar.resetButton.click()
        check((display.GetWindow(), display.GetLevel(), display.GetColorNodeID()) == original_grey, "original-grey button restores the source display settings")
        check(bar.windowLevelLabel.text == f"窗宽 {original_grey[0]:.1f}  窗位 {original_grey[1]:.1f}",
              "restoring original grey also restores the displayed numeric values")
        w.selectCase(1)
        next_display = w.volume.GetDisplayNode()
        next_text = f"窗宽 {next_display.GetWindow():.1f}  窗位 {next_display.GetLevel():.1f}"
        display.SetWindowLevel(321.0, 123.0)
        check(bar.windowLevelLabel.text == next_text, "case switching rebinds the readout and ignores changes on the old image")
        w.selectCase(0)
        display = w.volume.GetDisplayNode()
        bar.transformButton.click()
        origin = list(red.mrmlSliceNode().GetXYZOrigin())
        matrix = str(red.mrmlSliceNode().GetSliceToRAS())
        slicer.util.clickAndDrag(view, "Middle", centre, (centre[0] + 40, centre[1] + 30), steps=10)
        check(list(red.mrmlSliceNode().GetXYZOrigin()) != origin or str(red.mrmlSliceNode().GetSliceToRAS()) != matrix,
              "native middle-button dragging pans the radiograph in transform mode")
        bar.fitButton.click()
        w.circleButton.click()
        check(not bar.transformButton.isChecked() and not bar.windowLevelButton.isChecked(), "placing a point clears both viewing-mode indicators")
        bar.windowLevelButton.click()
        check(w.markups.placing_id is None and not w.markups.nodes, "returning to a viewing tool safely cancels an unplaced point")
        bar.transformButton.click()
        case_id = w.currentRecord["case_id"]
        volume_id = w.volume.GetID()
        bar.fullButton.click()
        events(.2)
        native_bars = slicer.util.findChildren(widget=slicer.util.mainWindow(), className="QToolBar")
        check(bar.fullInterface and all(toolbar.isVisible() for toolbar in native_bars) and red.sliceController().isVisible(),
              "full interface restores native toolbars, module selection and view controls")
        check(slicer.util.mainWindow().grab().save(str(OUT / "viewer-full-preview.png")), "capture the full Slicer interface with the persistent image toolbar")
        slicer.util.selectModule("Volumes")
        check(bar.frame.isVisible() and bar.fullButton.isEnabled(), "the return switch stays available while using a different Slicer module")
        slicer.app.layoutManager().setLayout(slicer.vtkMRMLLayoutNode.SlicerLayoutFourUpView)
        bar.fullButton.click()
        events(.2)
        check(not bar.fullInterface and slicer.util.selectedModule() == "PneumoconiosisAnnotator"
              and all(not toolbar.isVisible() for toolbar in native_bars) and not red.sliceController().isVisible(),
              "returning to compact mode restores the annotation module and hides native chrome")
        check(w.currentRecord["case_id"] == case_id and w.volume.GetID() == volume_id and w._zonesFromUI() == empty_zones(),
              "viewing modes and full-interface round trip preserve the selected case and all annotations")
        control = w.gradeSliders["RU"]
        mouse(control.slider, qt.QEvent.MouseButtonPress, 0)
        mouse(control.slider, qt.QEvent.MouseButtonRelease, 0)
        check(w._zonesFromUI()["RU"]["grade"] == "0/-", "clicking the unmoved first slider handle explicitly records the lowest grade")
        check(control.property("completionState") == "ready" and w.gradeSliders["RM"].property("completionState") == "missing",
              "each lung-zone row turns green independently while unread rows stay red")
        key = qt.QKeyEvent(qt.QEvent.KeyPress, qt.Qt.Key_Right, qt.Qt.NoModifier)
        slicer.app.sendEvent(control.slider, key)
        check(w._zonesFromUI()["RU"]["grade"] == "0/0", "native keyboard arrows select the adjacent small grade")
        wheel = qt.QWheelEvent(qt.QPointF(10, 10), qt.QPointF(10, 10), qt.QPoint(0, 0), qt.QPoint(0, 120),
                              qt.Qt.NoButton, qt.Qt.NoModifier, qt.Qt.NoScrollPhase, False)
        slicer.app.sendEvent(control.slider, wheel)
        check(control.value == "0/0" and control.slider.value == 1, "scrolling over a slider cannot accidentally change a grade")
        for step in (8, 2, 11, 0):
            mouse(control.slider, qt.QEvent.MouseButtonPress, step)
            mouse(control.slider, qt.QEvent.MouseButtonRelease, step)
            check(control.slider.value == step and control.value == GRADES[step]
                  and w.store.get(w.cases[0]["case_id"], "reader-01")["zones"]["RU"]["grade"] == GRADES[step],
                  f"clicking the track jumps directly to grade {GRADES[step]} and saves it")
        control.unreadButton.click()
        mouse(control.slider, qt.QEvent.MouseButtonPress, 0)
        mouse(control.slider, qt.QEvent.MouseMove, 11)
        check(control.value == "0/-" and control.valueLabel.text == "3/+", "dragging previews the exact grade without writing intermediate ratings")
        mouse(control.slider, qt.QEvent.MouseButtonRelease, 11)
        check(control.value == "3/+" and w.store.get(w.cases[0]["case_id"], "reader-01")["zones"]["RU"]["grade"] == "3/+",
              "releasing a native mouse drag saves the selected slider grade immediately")
        mouse(control.slider, qt.QEvent.MouseButtonPress, 3)
        mouse(control.slider, qt.QEvent.MouseMove, 7)
        mouse(control.slider, qt.QEvent.MouseButtonRelease, 7)
        check(control.value == GRADES[7] and control.slider.value == 7
              and w.store.get(w.cases[0]["case_id"], "reader-01")["zones"]["RU"]["grade"] == GRADES[7],
              "a track click can continue into a drag and save the final grade")
        # All twelve valid grades pass through native slider signals into persistence.
        for i, zone in enumerate(ZONES):
            rate(w, zone, GRADES[i])
        check([w.gradeSliders[zone].value for zone in ZONES] == list(GRADES[:6]), "the six lung-zone sliders remain independent")
        first_id = w.cases[0]["case_id"]
        for grade in (1, 2, 3, 4, None, 1):
            w.features.qualityChoices.buttons[grade].click()
            assert w.features.qualityChoices.value == grade
            assert sum(button.isChecked() for button in w.features.qualityChoices.buttons.values()) == 1
        events(.65)
        check(w.store.get(first_id, "reader-01")["quality"]["grade"] == 1,
              "quality radio choices, including clearing back to unread, autosave without changing the six ratings")
        saved = w.store.get(first_id, "reader-01")
        check("note" not in saved, "new records contain no text note field")
        first_zones = w._zonesFromUI()
        check(saved["zones"] == first_zones, "grade changes autosave immediately")
        check(w.completeButton.property("completionState") == "ready", "satisfied required fields turn the completion action green")
        check(w.completeButton.grab().save(str(OUT / "completion-ready-button.png")), "capture the green completion action")
        check(w.completeCase() and w.currentIndex == 1, "complete persists and switches to next case")
        check(w.features.qualityChoices.value is None, "case switching restores unread quality without inheriting the previous selection")
        check(w.completeButton.property("completionState") == "missing"
              and w.features.qualityWidget.property("completionState") == "missing", "case switching recomputes the next case's missing requirements")
        for i, zone in enumerate(ZONES):
            rate(w, zone, GRADES[i + 6])
        second_zones = w._zonesFromUI()
        w.gradeSliders["LL"].unreadButton.click()
        check(w._zonesFromUI()["LL"]["state"] == "unread"
              and all(w._zonesFromUI()[zone] == second_zones[zone] for zone in ZONES if zone != "LL")
              and w.store.get(w.cases[1]["case_id"], "reader-01")["zones"]["LL"]["state"] == "unread",
              "a zone can be cleared back to unread and autosaved without clearing another zone")
        rate(w, "LL", GRADES[-1])
        check(w.flushSave(), "all twelve small grades accepted in actual Slicer UI")
        check(w.selectCase(0) and w._zonesFromUI() == first_zones, "switch back restores exact six-zone ratings")
        check(w.store.get(first_id, "reader-01")["status"] == "complete", "completion status survives navigation")
        w.readerCombo.setCurrentIndex(w.readerCombo.findData("reader-02"))
        check(w.changeReader() and w._zonesFromUI() == empty_zones(), "second reader starts with independent unread ratings")
        w.readerCombo.setCurrentIndex(w.readerCombo.findData("reader-01"))
        check(w.changeReader() and w._zonesFromUI() == first_zones, "switching reader restores original record")
        w.store.connection.executescript("CREATE TEMP TRIGGER reject_save BEFORE INSERT ON annotations BEGIN SELECT RAISE(ABORT, 'QA forced save failure'); END;")
        w.features.qualityChoices.buttons[2].click()
        check(not w.selectCase(1) and w.currentIndex == 0, "save failure blocks case switch and retains current input")
        check(w.saveLabel.isVisible() and w.retryButton.isVisible(), "actual save errors and retry remain visible")
        check(w.completeButton.property("completionState") == "missing" and "save" in w.updateCompletionControls(),
              "storage failure turns completion red even when all form inputs are valid")
        w.readerCombo.setCurrentIndex(w.readerCombo.findData("reader-02"))
        check(w.currentReader == "reader-01" and w.readerCombo.currentData == "reader-01",
              "a failed save blocks reader switching and restores the original reader selection")
        close = qt.QCloseEvent()
        check(w.closeGuard.eventFilter(slicer.util.mainWindow(), close) and not close.isAccepted(), "save failure blocks application close")
        w.store.connection.execute("DROP TRIGGER reject_save")
        check(w.flushSave(), "retry recovers buffered input after storage failure")
        check(w.currentRecord["quality"]["grade"] == 2, "storage retry preserves the quality selection made before the failure")
        check(w.saveLabel.isVisible() and "已自动保存" in w.saveLabel.text and not w.retryButton.isVisible(), "successful saves keep the saved status visible and hide retry")
        check(w.completeButton.property("completionState") == "ready", "a successful storage retry restores green completion")
        # Non-scorable is a valid result without mandatory text or audio.
        w.selectCase(2)
        w.features.qualityChoices.buttons[4].click()
        for zone in ZONES:
            rate(w, zone, "0/0")
        w.gradeSliders["RU"].notScorableButton.click()
        check(w.gradeSliders["RU"].property("completionState") == "ready" and w.ratingsWidget.property("completionState") == "ready",
              "choosing non-scorable immediately turns the lung zone and rating group green")
        check(not w.features.audio and w.completeButton.property("completionState") == "ready",
              "a non-scorable case can complete without a mandatory recording")
        check(w.completeCase(), "non-scorable status completes and advances without text input")
        for index in range(len(w.cases)):
            check(w.selectCase(index), f"native DICOM reader loads sample {index + 1:02d}")
            check(list(w.volume.GetImageData().GetDimensions()) == [w.cases[index]["columns"], w.cases[index]["rows"], 1], f"sample {index + 1:02d} retains original pixel dimensions")
        check(all(file_sha256(c["file_path"]) == hashes[c["case_id"]] for c in w.cases), "all source DICOM files unchanged")
        bundle = w.exportResults(OUT / "exports") / w.currentReader
        csv_path, json_path = (bundle / (w.currentReader + suffix) for suffix in (".csv", ".json"))
        rows = list(csv.DictReader(csv_path.read_text(encoding="utf-8-sig").splitlines()))
        package = json.loads(json_path.read_text())
        check(len(rows) == 66 and len(package["records"]) == 11, "CSV and JSON export include all 11 cases and 66 zones")
        check("note" not in rows[0] and all("note" not in record for record in package["records"]),
              "CSV and JSON omit the removed text-note field")
        exported_grades = {z["grade"] for r in package["records"] for z in r["zones"].values() if z["grade"]}
        check(exported_grades == set(GRADES), "all twelve grades export losslessly")
        check(package["records"][2]["zones"]["RU"] == {"state": "not_scorable", "grade": None}, "non-scorable exports as null, never zero")
        # Return to an unread case for a clean UI preview without invented clinical grades.
        w.readerCombo.setCurrentIndex(w.readerCombo.findData("reader-03"))
        w.changeReader()
        w.selectCase(0)
        window = slicer.util.mainWindow()
        window.resize(1400, 950)
        w.viewerToolbar.setFullInterface(False)
        events(0.3)
        w.fitImage()
        events(0.2)
        check(window.grab().save(str(OUT / "module-preview.png")), "capture native Slicer module preview")
        check(w.ratingsWidget.grab().save(str(OUT / "slider-preview.png")), "capture the compact six-slider panel")
        REPORT["success"] = True
    except Exception:
        REPORT["success"] = False
        REPORT["error"] = traceback.format_exc()
        traceback.print_exc()
    finally:
        (OUT / "smoke-result.json").write_text(json.dumps(REPORT, ensure_ascii=False, indent=2))
        slicer.util.exit(0 if REPORT.get("success") else 1)


run()
