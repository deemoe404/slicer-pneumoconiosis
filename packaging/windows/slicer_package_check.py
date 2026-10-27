"""Exercise the installed GUI and its portable worker using only the bundled demo."""
import json
import os
from pathlib import Path
import sys
import time
import traceback

import qt
import slicer

ROOT = Path(os.environ.get("PNEUMO_APPLICATION_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from render_check import capture_rendered_view
OUT = Path(os.environ["PNEUMO_TEST_OUTPUT"])
OUT.mkdir(parents=True, exist_ok=True)
REPORT = {"status": "failed", "checks": [], "synthetic_only": True}
unexpected_dialogs = []


def dismiss_unexpected_dialogs():
    dialog = qt.QApplication.activeModalWidget()
    if dialog is not None and dialog.objectName != "PneumoCaseDirectoryDialog":
        unexpected_dialogs.append(str(dialog.windowTitle))
        dialog.close()


dialog_watch = qt.QTimer()
dialog_watch.connect("timeout()", dismiss_unexpected_dialogs)
dialog_watch.start(50)


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
    check("--testing" not in slicer.app.arguments(), "GUI starts with normal application flags, without Slicer testing mode")
    startup = ROOT / "scripts/start.py"
    exec(compile(startup.read_text(encoding="utf-8"), str(startup), "exec"), {"__file__": str(startup), "__name__": "package_startup"})
    status_path = OUT / "application-data/startup-status.json"
    deadline = time.monotonic() + 30
    while not status_path.exists() and time.monotonic() < deadline:
        events(.1)
    status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
    check(status.get("initialized") is True and status.get("process_id") == os.getpid(),
          "normal application startup initializes the external user data directory")
    slicer.util.selectModule("PneumoconiosisAnnotator")
    w = slicer.app.moduleManager().module("PneumoconiosisAnnotator").widgetRepresentation().self()
    from PneumoconiosisLib.runtime import packaged, python_executable
    check(w.dataRoot.resolve() == (OUT / "application-data").resolve(), "GUI uses the isolated test profile")
    if packaged(ROOT):
        check(not w.dataRoot.resolve().is_relative_to(ROOT.resolve()), "writable annotations are outside the installation directory")

    # The compact UI must also work when optional native panels are unavailable.
    probe_panels = slicer.util.findChildren(slicer.util.mainWindow(), name="DataProbeCollapsibleWidget")
    probe = getattr(slicer.modules, "DataProbeInstance", None)
    info = probe.infoWidget if probe is not None else None
    try:
        for panel in probe_panels:
            panel.setObjectName("PneumoTemporarilyUnavailableDataProbe")
        if probe is not None:
            probe.infoWidget = None
        w.enter()
        w.viewerToolbar.setFullInterface(True)
        w.viewerToolbar.setFullInterface(False)
        check(True, "startup and full-interface switching tolerate an unavailable native Data Probe")
    finally:
        for panel in probe_panels:
            panel.setObjectName("DataProbeCollapsibleWidget")
        if probe is not None:
            probe.infoWidget = info

    source = Path(os.environ["PNEUMO_TEST_DICOM_DIRECTORY"])
    picker_visits = []
    def select_folder():
        for dialog in slicer.util.findChildren(slicer.util.mainWindow(), name="PneumoCaseDirectoryDialog"):
            if dialog.isVisible():
                picker_visits.append(str(dialog.windowTitle))
                dialog.setDirectory(str(source.parent))
                dialog.selectFile(str(source))
                dialog.accept()
    selector = qt.QTimer()
    selector.connect("timeout()", select_folder)
    selector.start(50)
    try:
        w.openButton.click()
    finally:
        selector.stop()
    check(len(picker_visits) == 1 and w.currentRecord is not None and len(w.cases) == 1,
          "Open Directory button and its real folder picker import a Unicode archive with Mac metadata")
    check(all("__MACOSX" not in Path(case["file_path"]).parts
              and not Path(case["file_path"]).name.startswith("._") for case in w.cases),
          "GUI imports only the real synthetic chest radiograph")
    check(not unexpected_dialogs, "startup and folder selection show no unexpected native error dialogs")
    REPORT.update(slicer=slicer.app.applicationVersion, revision=slicer.app.repositoryRevision,
                  data_root=str(w.dataRoot), project=str(w.store.directory))
    REPORT["slicer_home"] = str(slicer.app.slicerHome)
    if packaged(ROOT):
        expected_slicer = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["slicer"]
        check(Path(REPORT["slicer_home"]).resolve() == (ROOT / "Slicer").resolve(), "Slicer runs from the relocated installation")
        revision = str(REPORT["revision"])
        check(REPORT["slicer"].startswith(expected_slicer["version"]) and len(revision) >= 7
              and expected_slicer["source_revision"].startswith(revision),
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
    REPORT.update(capture_rendered_view(view, OUT / "installed-view.png"))
    check(True, "installed Slicer renders a nonblank radiograph with a Qt-owned OpenGL 3.2+ context")
    if os.environ.get("PNEUMO_EXPECT_SOFTWARE_GL") == "1":
        check("llvmpipe" in REPORT["renderer"].lower(), "GUI check uses the isolated Mesa software renderer")
    check(window.grab().save(str(OUT / "installed-gui.png")), "installed GUI screenshot captured")
    REPORT["record"] = w.currentRecord
    check(not unexpected_dialogs, "the complete GUI session has no unexpected native error dialogs")
    w.cleanup()
    REPORT["status"] = "passed"
except Exception:
    REPORT["error"] = traceback.format_exc()
    print(REPORT["error"], flush=True)
finally:
    dialog_watch.stop()
    REPORT["unexpected_dialogs"] = unexpected_dialogs
    (OUT / "gui-report.json").write_text(json.dumps(REPORT, ensure_ascii=False, indent=2), encoding="utf-8")
    slicer.util.exit(0 if REPORT["status"] == "passed" else 1)
