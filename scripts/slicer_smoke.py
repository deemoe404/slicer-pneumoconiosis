"""Exercise actual Slicer widgets, source images, autosave and failure recovery in an isolated project."""
import csv
import json
import sys
import time
import traceback
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
        qa_store = OUT / "integration-store"
        for suffix in ("", "-wal", "-shm"):
            (qa_store / ("annotations.sqlite3" + suffix)).unlink(missing_ok=True)
        w.dataRoot = OUT / "settings"
        check(all(not control.slider.isEnabled() for control in w.gradeSliders.values()),
              "rating sliders stay disabled until a case is loaded")
        check(w.openDirectory(ROOT.parent / "2023Sample", qa_store, "qa-reader"), "open current DICOM samples")
        check(len(w.cases) == 11, "11 eligible single-frame DX cases")
        hashes = {c["case_id"]: c["source_sha256"] for c in w.cases}
        check(w._zonesFromUI() == empty_zones(), "initial ratings are unread, never auto-filled")
        check(all(control.slider.minimum == 0 and control.slider.maximum == 11 and control.slider.singleStep == 1
                  for control in w.gradeSliders.values()), "each lung zone has exactly twelve discrete slider grades")
        check(w.features.qualityChoices.value is None and len(w.features.qualityChoices.buttons) == 5,
              "quality exposes all four grades and unread as radio choices")
        events(.2)
        check([label.text for label in w.gradeRuler.labels] == list(GRADES)
              and all(a.geometry.right() < b.geometry.left() for a, b in zip(w.gradeRuler.labels, w.gradeRuler.labels[1:])),
              "the shared ruler displays all twelve small-grade labels without overlap")
        control = w.gradeSliders["RU"]
        mouse(control.slider, qt.QEvent.MouseButtonPress, 0)
        mouse(control.slider, qt.QEvent.MouseButtonRelease, 0)
        check(w._zonesFromUI()["RU"]["grade"] == "0/-", "clicking the unmoved first slider handle explicitly records the lowest grade")
        key = qt.QKeyEvent(qt.QEvent.KeyPress, qt.Qt.Key_Right, qt.Qt.NoModifier)
        slicer.app.sendEvent(control.slider, key)
        check(w._zonesFromUI()["RU"]["grade"] == "0/0", "native keyboard arrows select the adjacent small grade")
        wheel = qt.QWheelEvent(qt.QPointF(10, 10), qt.QPointF(10, 10), qt.QPoint(0, 0), qt.QPoint(0, 120),
                              qt.Qt.NoButton, qt.Qt.NoModifier, qt.Qt.NoScrollPhase, False)
        slicer.app.sendEvent(control.slider, wheel)
        check(control.value == "0/0" and control.slider.value == 1, "scrolling over a slider cannot accidentally change a grade")
        control.unreadButton.click()
        mouse(control.slider, qt.QEvent.MouseButtonPress, 0)
        mouse(control.slider, qt.QEvent.MouseMove, 11)
        check(control.value == "unread" and control.valueLabel.text == "3/+", "dragging previews the exact grade without writing intermediate ratings")
        mouse(control.slider, qt.QEvent.MouseButtonRelease, 11)
        check(control.value == "3/+" and w.store.get(w.cases[0]["case_id"], "qa-reader")["zones"]["RU"]["grade"] == "3/+",
              "releasing a native mouse drag saves the selected slider grade immediately")
        # All twelve valid grades pass through native slider signals into persistence.
        for i, zone in enumerate(ZONES):
            rate(w, zone, GRADES[i])
        check([w.gradeSliders[zone].value for zone in ZONES] == list(GRADES[:6]), "the six lung-zone sliders remain independent")
        w.noteEdit.setPlainText("仅 QA：自动保存与恢复测试")
        events(0.65)
        first_id = w.cases[0]["case_id"]
        for grade in (1, 2, 3, 4, None, 1):
            w.features.qualityChoices.buttons[grade].click()
            assert w.features.qualityChoices.value == grade
            assert sum(button.isChecked() for button in w.features.qualityChoices.buttons.values()) == 1
        events(.65)
        check(w.store.get(first_id, "qa-reader")["quality"]["grade"] == 1,
              "quality radio choices, including clearing back to unread, autosave without changing the six ratings")
        saved = w.store.get(first_id, "qa-reader")
        check(saved["note"].startswith("仅 QA"), "debounced note autosaves without a save button")
        first_zones = w._zonesFromUI()
        check(saved["zones"] == first_zones, "grade changes autosave immediately")
        check(w.completeCase() and w.currentIndex == 1, "complete persists and switches to next case")
        check(w.features.qualityChoices.value is None, "case switching restores unread quality without inheriting the previous selection")
        for i, zone in enumerate(ZONES):
            rate(w, zone, GRADES[i + 6])
        second_zones = w._zonesFromUI()
        w.gradeSliders["LL"].unreadButton.click()
        check(w._zonesFromUI()["LL"]["state"] == "unread"
              and all(w._zonesFromUI()[zone] == second_zones[zone] for zone in ZONES if zone != "LL")
              and w.store.get(w.cases[1]["case_id"], "qa-reader")["zones"]["LL"]["state"] == "unread",
              "a zone can be cleared back to unread and autosaved without clearing another zone")
        rate(w, "LL", GRADES[-1])
        check(w.flushSave(), "all twelve small grades accepted in actual Slicer UI")
        check(w.selectCase(0) and w._zonesFromUI() == first_zones, "switch back restores exact six-zone ratings")
        check(w.store.get(first_id, "qa-reader")["status"] == "complete", "completion status survives navigation")
        w.readerEdit.setText("qa-second-reader")
        check(w.changeReader() and w._zonesFromUI() == empty_zones(), "second reader starts with independent unread ratings")
        w.readerEdit.setText("qa-reader")
        check(w.changeReader() and w._zonesFromUI() == first_zones, "switching reader restores original record")
        w.store.connection.executescript("CREATE TEMP TRIGGER reject_save BEFORE INSERT ON annotations BEGIN SELECT RAISE(ABORT, 'QA forced save failure'); END;")
        w.noteEdit.setPlainText("仅 QA：模拟失败后保留输入")
        check(not w.selectCase(1) and w.currentIndex == 0, "save failure blocks case switch and retains current input")
        close = qt.QCloseEvent()
        check(w.closeGuard.eventFilter(slicer.util.mainWindow(), close) and not close.isAccepted(), "save failure blocks application close")
        w.store.connection.execute("DROP TRIGGER reject_save")
        check(w.flushSave(), "retry recovers buffered input after storage failure")
        # Non-scorable is explicitly different from zero and requires a reason at completion.
        w.selectCase(2)
        w.features.qualityChoices.buttons[4].click()
        for zone in ZONES:
            rate(w, zone, "0/0")
        w.gradeSliders["RU"].notScorableButton.click()
        check(not w.completeCase(), "not-scorable completion requires a reason")
        w.noteEdit.setPlainText("仅 QA：右上遮挡，其余肺区可评")
        check(w.completeCase(), "non-scorable status and its note can complete with quality grade four and no defect reason")
        for index in range(len(w.cases)):
            check(w.selectCase(index), f"native DICOM reader loads sample {index + 1:02d}")
            check(list(w.volume.GetImageData().GetDimensions()) == [w.cases[index]["columns"], w.cases[index]["rows"], 1], f"sample {index + 1:02d} retains original pixel dimensions")
        check(all(file_sha256(c["file_path"]) == hashes[c["case_id"]] for c in w.cases), "all source DICOM files unchanged")
        csv_path, json_path, spatial_path = w.exportResults(OUT / "exports")
        rows = list(csv.DictReader(csv_path.read_text(encoding="utf-8-sig").splitlines()))
        package = json.loads(json_path.read_text())
        check(len(rows) == 66 and len(package["records"]) == 11, "CSV and JSON export include all 11 cases and 66 zones")
        exported_grades = {z["grade"] for r in package["records"] for z in r["zones"].values() if z["grade"]}
        check(exported_grades == set(GRADES), "all twelve grades export losslessly")
        check(package["records"][2]["zones"]["RU"] == {"state": "not_scorable", "grade": None}, "non-scorable exports as null, never zero")
        # Return to an unread case for a clean UI preview without invented clinical grades.
        w.readerEdit.setText("preview-unread")
        w.changeReader()
        w.selectCase(0)
        window = slicer.util.mainWindow()
        window.resize(1400, 950)
        slicer.util.setToolbarsVisible(False)
        slicer.util.setDataProbeVisible(False)
        slicer.util.setModulePanelTitleVisible(False)
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
