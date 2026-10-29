"""Exercise the installed GUI and its portable worker using only the bundled demo."""
import json
import os
from pathlib import Path
import platform
import sys
import time
import traceback

import qt
import slicer
import vtk

ROOT = Path(os.environ.get("PNEUMO_APPLICATION_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from render_check import capture_rendered_view
from reference_check import check_reference_view
from language_check import check_language
from segmentation_check import check_segmentation
OUT = Path(os.environ["PNEUMO_TEST_OUTPUT"])
OUT.mkdir(parents=True, exist_ok=True)
REPORT = {"status": "failed", "checks": [], "synthetic_only": True,
          "platform": platform.platform(), "architecture": platform.machine()}
unexpected_dialogs = []


def dismiss_unexpected_dialogs():
    dialog = qt.QApplication.activeModalWidget()
    if dialog is not None and dialog.objectName not in (
            "PneumoCaseDirectoryDialog", "PneumoReferenceDirectoryDialog", "PneumoNewProjectDialog",
            "PneumoOpenProjectDialog", "PneumoLabelmeDirectoryDialog", "PneumoBusyOverlay"):
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
    if sys.platform == "win32":
        import ctypes
        # GetVersionEx in Slicer's process can report Windows 8 on a newer host.
        REPORT["windows_version"] = platform.win32_ver()[1]
        REPORT["windows_active_code_page"] = ctypes.windll.kernel32.GetACP()
        check(REPORT["windows_active_code_page"] == 65001,
              "Slicer uses a UTF-8 process code page for Unicode paths")
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
    w = slicer.app.moduleManager().module("PneumoconiosisAnnotator").widgetRepresentation().self()
    check(slicer.util.selectedModule() == "PneumoconiosisAnnotator" and w.headerWidget.isVisible(),
          "normal startup displays the annotation panel without test-driven module selection")
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
    check(w.sourceDirectory is None and w.store is None and w.volume is None, "launcher starts empty")
    project = OUT / "annotation-project"
    project.mkdir()
    picker_visits = []
    def select_folder():
        for dialog in slicer.util.findChildren(slicer.util.mainWindow(), name="PneumoCaseDirectoryDialog"):
            if dialog.isVisible():
                picker_visits.append(str(dialog.windowTitle))
                # In Directory mode, accept the current directory. selectFile()
                # can leave Windows in the parent directory without selecting it.
                dialog.setDirectory(str(source))
                dialog.accept()
        for dialog in slicer.util.findChildren(slicer.util.mainWindow(), name="PneumoNewProjectDialog"):
            if dialog.isVisible():
                picker_visits.append(str(dialog.windowTitle))
                dialog.setDirectory(str(project))
                dialog.accept()
    selector = qt.QTimer()
    selector.connect("timeout()", select_folder)
    selector.start(50)
    try:
        w.openButton.click()
        check(w.store is None and w.volume is None, "selecting images waits for a project")
        w.newProjectButton.click()
    finally:
        selector.stop()
    selected = str(w.sourceDirectory) if w.sourceDirectory else None
    REPORT["folder_picker"] = {"expected": str(source), "selected": selected,
                              "visits": len(picker_visits), "case_count": len(w.cases)}
    check(bool(picker_visits) and selected is not None and Path(selected).resolve() == source.resolve()
          and w.currentRecord is not None and len(w.cases) == 1,
          "Open Directory button and its real folder picker import a Unicode archive with Mac metadata")
    check(all("__MACOSX" not in Path(case["file_path"]).parts
              and not Path(case["file_path"]).name.startswith("._") for case in w.cases),
          "GUI imports only the real synthetic chest radiograph")
    check(not unexpected_dialogs, "startup and folder selection show no unexpected native error dialogs")
    REPORT.update(slicer=slicer.app.applicationVersion, revision=slicer.app.repositoryRevision,
                  data_root=str(w.dataRoot))
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
    REPORT["chinese_text"] = check_language(w, OUT / "language-check", check, events)
    REPORT["references"] = check_reference_view(w, OUT / "reference-check", check, events)
    from PneumoconiosisLib.core import GRADES
    check([label.text for label in w.gradeRuler.headings] == ["0", "1", "2", "3"]
          and [label.text for label in w.gradeRuler.labels] == list(GRADES),
          "installed rating ruler groups all twelve grades into four major levels")
    slider = w.gradeSliders["RU"].slider
    for step in (8, 2, 11, 0):
        point = qt.QPoint(round((step + .5) * slider.width / len(GRADES)), slider.height // 2)
        for kind, held in ((qt.QEvent.MouseButtonPress, qt.Qt.LeftButton), (qt.QEvent.MouseButtonRelease, qt.Qt.NoButton)):
            event = qt.QMouseEvent(kind, qt.QPointF(point), qt.QPointF(slider.mapToGlobal(point)),
                                  qt.Qt.LeftButton, held, qt.Qt.NoModifier)
            slicer.app.sendEvent(slider, event)
        saved = w.store.get(w.currentRecord["case_id"], w.currentReader)
        check(slider.value == step and saved["zones"]["RU"]["grade"] == GRADES[step],
              "installed slider clicks jump to and save " + GRADES[step])
    # The compiled extension must be usable in the relocated, offline installation.
    w.circleButton.click()
    circle = w.markups.nodes[w.markups.selected_id]
    matrix = vtk.vtkMatrix4x4()
    w.volume.GetIJKToRASMatrix(matrix)
    columns, rows = w.cases[w.currentIndex]["columns"], w.cases[w.currentIndex]["rows"]
    for x in (.3, .4):
        world = matrix.MultiplyPoint([columns * x, rows * .4, 0, 1])
        circle.AddControlPointWorld(vtk.vtkVector3d(*world[:3]))
    check(circle.IsA("vtkMRMLMarkupsShapeNode") and circle.GetShapeName() == circle.Ring
          and circle.GetNumberOfDefinedControlPoints() == 3 and w.flushSave(),
          "bundled ExtraMarkups creates and saves a native circle offline")
    check("直径" in w.markups.labels[w.markups.selected_id].text,
          "native circle displays its measured diameter")
    destination = OUT / "LabelMe 导出"
    destination.mkdir()
    labelme_visits = []
    def choose_labelme():
        for dialog in slicer.util.findChildren(window, name="PneumoLabelmeDirectoryDialog"):
            if dialog.isVisible():
                labelme_visits.append(True)
                dialog.setDirectory(str(destination))
                dialog.accept()
    selector = qt.QTimer()
    selector.connect("timeout()", choose_labelme)
    selector.start(50)
    try:
        w.labelmeExport.button.click()
    finally:
        selector.stop()
    check(labelme_visits and w.labelmeExport.worker is not None,
          "installed LabelMe button selects a Unicode destination and starts its worker")
    deadline = time.monotonic() + 60
    while w.labelmeExport.worker is not None and time.monotonic() < deadline:
        events(.1)
    check(w.labelmeExport.worker is None and w.labelmeExport.last_result is not None and not w.labelmeExport.last_error,
          "installed portable worker completes LabelMe export offline")
    exported = Path(w.labelmeExport.last_result["directory"]) / w.currentReader
    label = json.loads((exported / (w.currentRecord["case_id"] + ".json")).read_text(encoding="utf-8"))
    check((exported / label["imagePath"]).is_file() and label["imageWidth"] == columns and label["imageHeight"] == rows
          and len(label["shapes"]) == 1 and label["shapes"][0]["source_kind"] == "circle",
          "installed LabelMe export pairs original-size PNG with the saved circle")
    w.deleteMarkupButton.click()
    w.features.qualityChoices.buttons[2].click()
    for slider in w.gradeSliders.values():
        slider.set_value("0/0")
    w.ratingChanged()
    check_segmentation(w, OUT / "segmentation-check", check, events)
    if w.features.jobs or w.features.partition.state is None:
        REPORT["model_error"] = {"message": str(w.saveLabel.text), "button_enabled": w.features.autoButton.isEnabled(),
                                 "jobs": list(w.features.jobs), "case": w.currentRecord["case_id"]}
    check(not w.features.jobs and w.features.partition.state is not None, "GUI runs the real lung model through QProcess")
    program = w.features.model_worker.process.program()
    check(Path(program).resolve() == python_executable(ROOT).resolve(), "QProcess uses the installed portable Python")
    check(w.features.partition.state["state"] == "pending_review", "machine partition awaits confirmation")
    check(w.features.partition.overlay.GetSegmentation().GetNumberOfSegments() == 6, "six native overlay segments are present")
    reference_view = slicer.app.layoutManager().sliceWidget("PneumoReference")
    check(reference_view.mrmlSliceCompositeNode().GetBackgroundVolumeID() == w.references.volume.GetID()
          and all(not node.GetDisplayNode().IsDisplayableInView(reference_view.mrmlSliceNode().GetID())
                  for node in (w.features.partition.lungs, w.features.partition.overlay, w.features.partition.anchors)),
          "model inference uses the case and its lung overlays do not appear on the standard")
    w.features.confirmButton.click()
    check(w.completeCase(), "quality, six scores and explicit confirmation complete the case")
    check(w.currentRecord["status"] == "complete" and w.flushSave(), "completed annotation is saved outside the program files")
    w.fitImage()
    w.references.fit()
    events(.1)
    bundle = w.exportResults(OUT / "gui-exports") / w.currentReader
    paths = [bundle / (w.currentReader + suffix) for suffix in (".csv", ".json", "-spatial.csv", "-images.csv")]
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
    # Import checks can switch projects. Bind the final record to the project
    # that actually owns it for the separate shutdown/reinstall verification.
    REPORT.update(record=w.currentRecord, project=str(w.store.directory))
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
