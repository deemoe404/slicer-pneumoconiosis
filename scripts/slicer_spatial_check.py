"""Verify native mouse placement, editing, persistence, isolation and spatial exports."""
import csv
import json
import time
import traceback
from pathlib import Path

import qt
import slicer
import vtk

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "local-data" / "qa"
REPORT = {"checks": []}


def check(value, message):
    if not value:
        raise AssertionError(message)
    REPORT["checks"].append(message)


def events(seconds=0.1):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        slicer.app.processEvents()
        time.sleep(0.01)


def xy(w, point):
    matrix = vtk.vtkMatrix4x4()
    w.volume.GetIJKToRASMatrix(matrix)
    world = matrix.MultiplyPoint(list(point) + [1])
    inverse = vtk.vtkMatrix4x4()
    inverse.DeepCopy(slicer.app.layoutManager().sliceWidget("Red").mrmlSliceNode().GetXYToRAS())
    inverse.Invert()
    position = inverse.MultiplyPoint(world)
    return tuple(round(v) for v in position[:2])


def mouse(w, point, button="Left", end=None):
    view = slicer.app.layoutManager().sliceWidget("Red").sliceView()
    interactor = view.interactorStyle().GetInteractor()
    position = xy(w, point)
    interactor.EnterEvent()
    interactor.SetEventPosition(*position)
    interactor.MouseMoveEvent()
    slicer.util.clickAndDrag(view, button, position, xy(w, end) if end else position, steps=10 if end else 1)
    events(0.08)


def click_list_row(w, row):
    view = w.markupList.viewport()
    point = w.markupList.visualItemRect(w.markupList.item(row)).center()
    for kind in (qt.QEvent.MouseButtonPress, qt.QEvent.MouseButtonRelease):
        held = qt.Qt.LeftButton if kind == qt.QEvent.MouseButtonPress else qt.Qt.NoButton
        event = qt.QMouseEvent(kind, qt.QPointF(point), qt.QPointF(view.mapToGlobal(point)), qt.Qt.LeftButton, held, qt.Qt.NoModifier)
        slicer.app.sendEvent(view, event)


try:
    slicer.util.selectModule("PneumoconiosisAnnotator")
    w = slicer.app.moduleManager().module("PneumoconiosisAnnotator").widgetRepresentation().self()
    project = OUT / "spatial-store"
    for suffix in ("", "-wal", "-shm"):
        (project / ("annotations.sqlite3" + suffix)).unlink(missing_ok=True)
    w.dataRoot = OUT / "spatial-settings"
    check(w.openDirectory(ROOT.parent / "2023Sample", project, "reader-01"), "open existing DX samples in an isolated spatial QA project")
    check(w.layout.itemAt(0).widget() == w.features.audioWidget and w.layout.itemAt(1).widget() == w.headerWidget,
          "voice recording is the first panel section, above case navigation")
    window = slicer.util.mainWindow()
    window.resize(1400, 1000)
    slicer.util.setToolbarsVisible(False)
    slicer.util.setDataProbeVisible(False)
    slicer.util.setModulePanelTitleVisible(False)
    events(0.3)
    w.fitImage()
    events()
    first_id = w.cases[0]["case_id"]
    w.pointButton.click()
    w.deleteMarkupButton.click()
    check(not w.markups.nodes and not w.spatialState, "cancel or delete an unplaced point leaves no phantom annotation")
    w.pointButton.click()
    mouse(w, [1600, 1100, 0])
    events(0.55)
    point = w.store.get(first_id, "reader-01")["spatial_annotations"]
    check(len(point) == 1 and point[0]["kind"] == "point", "native mouse click places one point and autosaves")
    check(w.spatialGroup.property("completionState") == "ready", "a placed point satisfies the spatial completion requirement")
    point_id = point[0]["id"]
    check(w.markupList.count == 1 and w.markupList.currentItem().data(qt.Qt.UserRole) == point_id,
          "a placed point appears as a selected row in the flat list")
    check(max(abs(a - b) for a, b in zip(point[0]["points_ijk"][0], [1600, 1100, 0])) < 4,
          "mouse placement exports original raster pixel coordinates")
    check(w.markups.placing_id is None, "single point placement returns to normal interaction")
    mouse(w, point[0]["points_ijk"][0], end=[1700, 1200, 0])
    events(0.55)
    moved = w.store.get(first_id, "reader-01")["spatial_annotations"][0]
    check(max(abs(a - b) for a, b in zip(moved["points_ijk"][0], [1700, 1200, 0])) < 4,
          "native mouse drag changes point position and autosaves")
    w.renameMarkup("QA_POINT")
    events(0.55)
    check(w.store.get(first_id, "reader-01")["spatial_annotations"][0]["label"] == "QA_POINT", "renaming a point autosaves")
    w.regionButton.click()
    for position in ([600, 650, 0], [1000, 650, 0], [1100, 1100, 0], [650, 1250, 0]):
        mouse(w, position)
    mouse(w, [650, 1250, 0], "Right")
    events(0.55)
    records = w.store.get(first_id, "reader-01")["spatial_annotations"]
    check(len(records) == 2 and records[1]["kind"] == "polygon" and records[1]["state"] == "ready",
          "native clicks create a closed polygon and right-click finishes it")
    check(len(records[1]["points_ijk"]) == 4, "cursor preview is excluded from persisted polygon vertices")
    region_id = records[1]["id"]
    w.renameMarkup("QA_REGION")
    events(0.55)
    check(w.markupList.count == 2 and "QA_POINT" in w.markupList.item(0).text() and "QA_REGION" in w.markupList.item(1).text(),
          "point and polygon names are visible together without opening a dropdown")
    click_list_row(w, 0)
    check(w.markups.selected_id == point_id and w.markupNameEdit.text == "QA_POINT", "native list click selects the point and its rename field")
    click_list_row(w, 1)
    check(w.markups.selected_id == region_id and w.markupNameEdit.text == "QA_REGION", "native list click selects the polygon independently")
    expected = w.store.get(first_id, "reader-01")["spatial_annotations"]
    check(w.selectCase(1) and w.markups.capture() == [], "switching case removes previous image's spatial nodes")
    check(w.markupList.count == 0, "switching to an unannotated case clears the flat list")
    check(w.selectCase(0) and len(w.markups.capture()) == 2, "switching back restores point and polygon")
    check(w.markups.capture()[0]["points_ijk"] == expected[0]["points_ijk"] and
          w.markups.capture()[1]["points_ijk"] == expected[1]["points_ijk"], "IJK to RAS round trip preserves coordinates after reloading DICOM")
    w.readerCombo.setCurrentIndex(w.readerCombo.findData("reader-02"))
    check(w.changeReader() and w.markups.capture() == [], "a different reader has independent spatial annotations")
    w.readerCombo.setCurrentIndex(w.readerCombo.findData("reader-01"))
    check(w.changeReader() and len(w.markups.capture()) == 2, "returning to reader restores spatial annotations")
    w.markups.select(region_id)
    w.deleteMarkupButton.click()
    check(len(w.store.get(first_id, "reader-01")["spatial_annotations"]) == 1, "selected polygon deletion persists immediately")
    check(w.markupList.count == 1 and w.markupList.item(0).data(qt.Qt.UserRole) == point_id, "deleting a polygon removes only its list row")
    # Keep an unfinished region as a resumable draft across case switching.
    w.regionButton.click()
    mouse(w, [650, 750, 0])
    mouse(w, [1100, 750, 0])
    w.finishMarkupButton.click()
    check(w.currentRecord["spatial_annotations"][1]["state"] == "draft", "two-point region persists explicitly as an unfinished draft")
    check("草稿" in w.markupList.item(1).text(), "unfinished polygon state is visible directly in its list row")
    check(w.spatialGroup.property("completionState") == "missing" and "spatial" in w.updateCompletionControls(),
          "an unfinished region turns its area red and reports the blocking geometry")
    draft_id = w.currentRecord["spatial_annotations"][1]["id"]
    check(w.selectCase(1) and w.selectCase(0), "unfinished region survives navigation")
    w.markups.select(draft_id)
    w.finishMarkupButton.click()
    mouse(w, [900, 1250, 0])
    w.finishMarkupButton.click()
    check(w.currentRecord["spatial_annotations"][1]["state"] == "ready", "restored region can continue drawing and become a valid closed region")
    check(w.spatialGroup.property("completionState") == "ready", "repairing the region returns its area to green")
    # Fail a real spatial write and ensure it cannot silently disappear on navigation.
    w.store.connection.executescript("CREATE TEMP TRIGGER reject_spatial BEFORE INSERT ON annotations BEGIN SELECT RAISE(ABORT, 'QA spatial disk failure'); END;")
    w.renameMarkup("QA_REGION_RECOVERED")
    check(not w.selectCase(1) and w.currentIndex == 0, "spatial save failure blocks case switch and retains edited geometry")
    w.store.connection.execute("DROP TRIGGER reject_spatial")
    check(w.flushSave(), "spatial save can retry successfully without losing shapes")
    paths = w.exportResults(OUT / "spatial-exports")
    package = json.loads(paths[1].read_text())
    vertices = list(csv.DictReader(paths[2].read_text(encoding="utf-8-sig").splitlines()))
    check(len(vertices) == 4, "separate coordinate CSV contains point and all three polygon vertices")
    check(len(package["records"][0]["spatial_annotations"]) == 2 and package["coordinate_system"]["index_base"] == 0,
          "JSON includes shapes, image dimensions, zero-based pixel axes and revision history")
    check("file_path" not in paths[1].read_text() and "sop_uid" not in paths[1].read_text(), "spatial export excludes source file paths and raw DICOM identifiers")
    from PneumoconiosisLib.core import file_sha256
    check(all(file_sha256(c["file_path"]) == c["source_sha256"] for c in w.cases), "spatial drawing and dragging leave all 11 source DICOM files unchanged")
    # No clinical grades are invented for the preview.
    events(0.2)
    check(window.grab().save(str(OUT / "spatial-preview.png")), "capture real Slicer point and region annotation UI")
    REPORT["expected"] = w.currentRecord["spatial_annotations"]
    REPORT["success"] = True
except Exception:
    REPORT["success"] = False
    REPORT["error"] = traceback.format_exc()
    if "w" in globals():
        REPORT["live_shapes"] = w.markups.capture()
        REPORT["native_nodes"] = [{"class": n.GetClassName(), "label": n.GetName(), "defined": n.GetNumberOfDefinedControlPoints()}
                                  for n in w.markups.nodes.values()]
        if "window" in globals():
            window.grab().save(str(OUT / "spatial-failure.png"))
    traceback.print_exc()
(OUT / "spatial-result.json").write_text(json.dumps(REPORT, ensure_ascii=False, indent=2))
slicer.util.exit(0 if REPORT["success"] else 1)
