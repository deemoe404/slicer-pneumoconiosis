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


def run():
    try:
        slicer.util.selectModule("PneumoconiosisAnnotator")
        REPORT["module_loaded"] = bool(slicer.app.moduleManager().module("PneumoconiosisAnnotator"))
        w = slicer.app.moduleManager().module("PneumoconiosisAnnotator").widgetRepresentation().self()
        qa_store = OUT / "integration-store"
        for suffix in ("", "-wal", "-shm"):
            (qa_store / ("annotations.sqlite3" + suffix)).unlink(missing_ok=True)
        w.dataRoot = OUT / "settings"
        check(all(not button.isEnabled() for choices in w.gradeChoices.values() for button in choices.buttons.values()),
              "rating radio buttons stay disabled until a case is loaded")
        check(w.openDirectory(ROOT.parent / "2023Sample", qa_store, "qa-reader"), "open current DICOM samples")
        check(len(w.cases) == 11, "11 eligible single-frame DX cases")
        hashes = {c["case_id"]: c["source_sha256"] for c in w.cases}
        check(w._zonesFromUI() == empty_zones(), "initial ratings are unread, never auto-filled")
        check(all(len(choices.buttons) == 14 for choices in w.gradeChoices.values()),
              "each lung zone exposes twelve grades plus unread and non-scorable radio choices")
        check(w.features.qualityChoices.value is None and len(w.features.qualityChoices.buttons) == 5,
              "quality exposes all four grades and unread as radio choices")
        # All twelve valid grades pass through native radio-button signals into persistence.
        for i, zone in enumerate(ZONES):
            w.gradeChoices[zone].buttons[GRADES[i]].click()
        check(all(sum(button.isChecked() for button in choices.buttons.values()) == 1 for choices in w.gradeChoices.values()),
              "the six radio columns remain independently exclusive")
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
            w.gradeChoices[zone].buttons[GRADES[i + 6]].click()
        second_zones = w._zonesFromUI()
        w.gradeChoices["LL"].buttons["unread"].click()
        check(w._zonesFromUI()["LL"]["state"] == "unread"
              and all(w._zonesFromUI()[zone] == second_zones[zone] for zone in ZONES if zone != "LL")
              and w.store.get(w.cases[1]["case_id"], "qa-reader")["zones"]["LL"]["state"] == "unread",
              "a zone can be cleared back to unread and autosaved without clearing another zone")
        w.gradeChoices["LL"].buttons[GRADES[-1]].click()
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
        w.features.qualityChoices.buttons[1].click()
        for zone in ZONES:
            w.gradeChoices[zone].buttons["0/0"].click()
        w.gradeChoices["RU"].buttons["not_scorable"].click()
        check(not w.completeCase(), "not-scorable completion requires a reason")
        w.noteEdit.setPlainText("仅 QA：右上遮挡，其余肺区可评")
        check(w.completeCase(), "not-scorable status and reason can be completed")
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
        REPORT["success"] = True
    except Exception:
        REPORT["success"] = False
        REPORT["error"] = traceback.format_exc()
        traceback.print_exc()
    finally:
        (OUT / "smoke-result.json").write_text(json.dumps(REPORT, ensure_ascii=False, indent=2))
        slicer.util.exit(0 if REPORT.get("success") else 1)


run()
