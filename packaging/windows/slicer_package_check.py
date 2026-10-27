"""Exercise the installed GUI and its portable worker using only the bundled demo."""
import json
import os
from pathlib import Path
import time
import traceback

import qt
import slicer

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ["PNEUMO_TEST_OUTPUT"])
OUT.mkdir(parents=True, exist_ok=True)
REPORT = {"status": "failed", "checks": [], "synthetic_only": True}


def check(value, message):
    if not value:
        raise AssertionError(message)
    REPORT["checks"].append(message)
    print("PASS: " + message, flush=True)


def events(seconds=.1):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        slicer.app.processEvents()
        time.sleep(.01)


try:
    slicer.util.selectModule("PneumoconiosisAnnotator")
    w = slicer.app.moduleManager().module("PneumoconiosisAnnotator").widgetRepresentation().self()
    from PneumoconiosisLib.runtime import python_executable
    check(not w.dataRoot.resolve().is_relative_to(ROOT.resolve()), "writable annotations are outside the installation directory")
    check(w.openDirectory(ROOT / "qa/dicom", w.dataRoot / "projects/package-smoke", "reader-01"), "installed module opens the bundled synthetic DICOM")
    REPORT.update(slicer=slicer.app.applicationVersion, revision=slicer.app.repositoryRevision,
                  data_root=str(w.dataRoot), project=str(w.store.directory))
    expected_slicer = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["slicer"]
    check(REPORT["slicer"].startswith(expected_slicer["version"]) and str(REPORT["revision"]) == expected_slicer["revision"],
          "installed GUI uses the pinned official Slicer build")
    window = slicer.util.mainWindow()
    window.resize(1400, 950)
    events(.3)
    w.fitImage()
    w.features.qualityChoices.buttons[2].click()
    for slider in w.gradeSliders.values():
        slider.set_value("0/0")
    w.ratingChanged()
    w.features.autoButton.click()
    deadline = time.monotonic() + 90
    while w.features.jobs and time.monotonic() < deadline:
        events(.1)
    check(not w.features.jobs and w.features.partition.state is not None, "GUI runs the real lung model through QProcess")
    program = w.features.model_worker.process.program()
    check(Path(program).resolve() == python_executable(ROOT).resolve(), "QProcess uses the installed portable Python")
    check(w.features.partition.state["state"] == "pending_review", "machine partition awaits confirmation")
    check(w.features.partition.overlay.GetSegmentation().GetNumberOfSegments() == 6, "six native overlay segments are present")
    w.features.confirmButton.click()
    check(w.completeCase(), "quality, six scores and explicit confirmation complete the case")
    check(w.currentRecord["status"] == "complete" and w.flushSave(), "completed annotation is saved outside the program files")
    paths = w.exportResults(OUT / "gui-exports")
    check(all(path.is_file() for path in paths), "installed GUI exports the CSV and JSON bundle")
    report_path = paths[1]
    record = json.loads(report_path.read_text(encoding="utf-8"))["records"][0]
    check(record["partition"]["state"] == "confirmed" and all(z["grade"] == "0/0" for z in record["zones"].values()),
          "GUI export contains the explicit fixture scores and confirmed partition")
    view = slicer.app.layoutManager().sliceWidget("Red").sliceView()
    view.scheduleRender()
    events(.5)
    render_window = view.renderWindow()
    check(bool(render_window.SupportsOpenGL()), "installed Slicer renders with OpenGL in the test environment")
    REPORT["renderer"] = render_window.ReportCapabilities()
    check(window.grab().save(str(OUT / "installed-gui.png")), "installed GUI screenshot captured")
    REPORT["record"] = w.currentRecord
    w.cleanup()
    REPORT["status"] = "passed"
except Exception:
    REPORT["error"] = traceback.format_exc()
    print(REPORT["error"], flush=True)
finally:
    (OUT / "gui-report.json").write_text(json.dumps(REPORT, ensure_ascii=False, indent=2), encoding="utf-8")
    slicer.util.exit(0 if REPORT["status"] == "passed" else 1)
