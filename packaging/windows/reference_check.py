"""Exercise reference comparison through the real GUI using synthetic DICOM only."""
import copy
import json
from pathlib import Path
import shutil

import numpy as np
import pydicom
from pydicom.uid import SecondaryCaptureImageStorage, generate_uid
import qt
import slicer
import vtk


def check_reference_view(w, output, check, events):
    output = Path(output)
    library = output / "标准片 Reference Folder"
    library.mkdir(parents=True)
    original_source = Path(json.loads((w.dataRoot / "last-project.json").read_text(encoding="utf-8"))["source"])
    original_project, original_index, original_reader = w.store.directory, w.currentIndex, w.currentReader
    original_case = w.currentRecord["case_id"]
    from PneumoconiosisLib.core import file_sha256
    from PneumoconiosisLib.reference_view import VIEW_NAME, LAYOUT_ID

    ds = pydicom.dcmread(w.cases[0]["file_path"])
    for name in ("WindowWidth", "WindowCenter"):
        if name in ds:
            del ds[name]
    ds.SOPClassUID = ds.file_meta.MediaStorageSOPClassUID = SecondaryCaptureImageStorage
    ds.SOPInstanceUID = ds.file_meta.MediaStorageSOPInstanceUID = generate_uid()
    ds.Modality = "OT"
    first = library / "01 合成标准片 OT.dcm"
    ds.save_as(first, enforce_file_format=True)
    ds.SOPInstanceUID = ds.file_meta.MediaStorageSOPInstanceUID = generate_uid()
    pixels = ds.pixel_array.astype(np.float64)
    pixels = np.clip((pixels - pixels.min()) / max(float(np.ptp(pixels)), 1) * 255, 0, 255).astype(np.uint8)
    ds.PixelData = pixels.tobytes()
    ds.BitsAllocated, ds.BitsStored, ds.HighBit = 8, 8, 7
    ds.Modality = "CR"
    second = library / "02 合成标准片 CR.dcm"
    ds.save_as(second, enforce_file_format=True)
    helpers = library / "__MACOSX"
    helpers.mkdir()
    shutil.copy2(first, helpers / "辅助.dcm")
    shutil.copy2(first, library / "._辅助.dcm")
    hashes = {path: file_sha256(path) for path in library.rglob("*") if path.is_file()}
    record_before = copy.deepcopy(w.currentRecord)
    visits = []

    def select_folder():
        for dialog in slicer.util.findChildren(slicer.util.mainWindow(), name="PneumoReferenceDirectoryDialog"):
            if dialog.isVisible():
                visits.append(str(dialog.windowTitle))
                dialog.setDirectory(str(library))
                dialog.accept()

    selector = qt.QTimer()
    selector.connect("timeout()", select_folder)
    selector.start(50)
    try:
        w.viewerToolbar.referenceFolderButton.click()
    finally:
        selector.stop()
    ref = w.references
    manager = slicer.app.layoutManager()
    check(bool(visits) and ref.root == library.resolve() and len(ref.images) == 2 and ref.visible,
          "reference folder button imports OT and CR from a Unicode folder while skipping Mac metadata")
    events(.2)
    check(manager.layout == LAYOUT_ID and manager.sliceWidget("Red").isVisible() and manager.sliceWidget(VIEW_NAME).isVisible(),
          "current radiograph and external reference use two visible native slice views")
    check(w.currentRecord == record_before and len(w.cases) == 1,
          "loading a reference does not create a case or alter the annotation")

    red, right = manager.sliceWidget("Red"), manager.sliceWidget(VIEW_NAME)
    check(red.mrmlSliceCompositeNode().GetBackgroundVolumeID() == w.volume.GetID()
          and right.mrmlSliceCompositeNode().GetBackgroundVolumeID() == ref.volume.GetID()
          and not right.mrmlSliceCompositeNode().GetDoPropagateVolumeSelection()
          and red.mrmlSliceNode().GetViewGroup() != right.mrmlSliceNode().GetViewGroup(),
          "the reference has a separate volume and does not participate in case selection or linked viewing")
    case_display, ref_display = w.volume.GetDisplayNode(), ref.volume.GetDisplayNode()
    case_gray = (case_display.GetWindow(), case_display.GetLevel())
    default_ref = (ref_display.GetWindow(), ref_display.GetLevel(), ref_display.GetColorNodeID())
    w.viewerToolbar.windowLevelButton.click()
    view = right.sliceView()
    center = (view.width // 2, view.height // 2)
    slicer.util.clickAndDrag(view, "Left", center, (center[0] + 50, center[1] + 25), steps=10)
    events(.1)
    check((ref_display.GetWindow(), ref_display.GetLevel()) != default_ref[:2]
          and (case_display.GetWindow(), case_display.GetLevel()) == case_gray,
          "native mouse window-level dragging changes only the reference")
    check(ref.windowLevelLabel.text == f"窗宽 {ref_display.GetWindow():.0f}  窗位 {ref_display.GetLevel():.0f}",
          "reference window-level values update immediately")
    ref.resetButton.click()
    check((ref_display.GetWindow(), ref_display.GetLevel(), ref_display.GetColorNodeID()) == default_ref,
          "reference original-gray button restores its own display")

    w.viewerToolbar.transformButton.click()
    case_fov, ref_fov = tuple(red.mrmlSliceNode().GetFieldOfView()), tuple(right.mrmlSliceNode().GetFieldOfView())
    wheel = qt.QWheelEvent(qt.QPointF(*center), qt.QPointF(view.mapToGlobal(qt.QPoint(*center))),
                          qt.QPoint(0, 0), qt.QPoint(0, 120), qt.Qt.NoButton, qt.Qt.NoModifier, qt.Qt.NoScrollPhase, False)
    slicer.app.sendEvent(view, wheel)
    events(.1)
    check(tuple(right.mrmlSliceNode().GetFieldOfView()) != ref_fov
          and tuple(red.mrmlSliceNode().GetFieldOfView()) == case_fov,
          "the mouse wheel zooms the reference without scrolling off its image or changing the case")
    ref.fitButton.click()

    ref.nextButton.click()
    check(ref.index == 1 and w.currentRecord == record_before and w.currentIndex == original_index,
          "reference navigation preserves the selected case and its scores")
    saved = json.loads(ref.settings_path.read_text(encoding="utf-8"))
    ref.clear()
    ref.restored = False
    ref.restore_once()
    check(ref.visible and ref.index == 1 and ref.images[ref.index]["relative_path"] == saved["selected"],
          "the library, selected reference and comparison state restore from the user profile")
    volume_id = ref.volume.GetID()
    ref.hideButton.click()
    check(not ref.visible and manager.layout == slicer.vtkMRMLLayoutNode.SlicerLayoutOneUpRedSliceView,
          "hiding comparison returns to the single case view")
    w.viewerToolbar.referenceButton.click()
    check(ref.visible and ref.volume.GetID() == volume_id and w.currentRecord["case_id"] == original_case,
          "comparison can reopen without reloading or replacing the case")
    w.viewerToolbar.fullButton.click()
    w.viewerToolbar.fullButton.click()
    check(ref.visible and manager.layout == LAYOUT_ID and right.mrmlSliceCompositeNode().GetBackgroundVolumeID() == volume_id,
          "returning from the full Slicer interface retains reference comparison")

    # Two synthetic cases exercise the real DICOM plugin's selection propagation.
    cases = output / "two synthetic cases"
    cases.mkdir()
    original = Path(w.cases[0]["file_path"])
    shutil.copy2(original, cases / "case-a.dcm")
    another = pydicom.dcmread(original)
    another.SOPInstanceUID = another.file_meta.MediaStorageSOPInstanceUID = generate_uid()
    another.save_as(cases / "case-b.dcm", enforce_file_format=True)
    check(w.openDirectory(cases) and len(w.cases) == 2 and w.selectCase(1), "two isolated synthetic cases can be selected during comparison")
    check(ref.volume.GetID() == volume_id and right.mrmlSliceCompositeNode().GetBackgroundVolumeID() == volume_id,
          "loading and switching DICOM cases does not replace the displayed standard")

    w.pointButton.click()
    node = w.markups.nodes[w.markups.selected_id]
    view = right.sliceView()
    point = qt.QPoint(view.width // 2, view.height // 2)
    for kind in (qt.QEvent.MouseButtonPress, qt.QEvent.MouseButtonRelease):
        held = qt.Qt.LeftButton if kind == qt.QEvent.MouseButtonPress else qt.Qt.NoButton
        event = qt.QMouseEvent(kind, qt.QPointF(point), qt.QPointF(view.mapToGlobal(point)), qt.Qt.LeftButton, held, qt.Qt.NoModifier)
        slicer.app.sendEvent(view, event)
    events(.1)
    check(node.GetNumberOfDefinedControlPoints() == 0,
          "clicking the reference during point placement cannot create a case annotation")
    red = manager.sliceWidget("Red")
    view = red.sliceView()
    matrix = vtk.vtkMatrix4x4()
    w.volume.GetIJKToRASMatrix(matrix)
    dims = w.volume.GetImageData().GetDimensions()
    world = matrix.MultiplyPoint([dims[0] * .5, dims[1] * .5, 0, 1])
    inverse = vtk.vtkMatrix4x4()
    inverse.DeepCopy(red.mrmlSliceNode().GetXYToRAS())
    inverse.Invert()
    position = tuple(round(value) for value in inverse.MultiplyPoint(world)[:2])
    interactor = view.interactorStyle().GetInteractor()
    interactor.EnterEvent()
    interactor.SetEventPosition(*position)
    interactor.MouseMoveEvent()
    slicer.util.clickAndDrag(view, "Left", position, position, steps=1)
    events(.1)
    check(node.GetNumberOfDefinedControlPoints() == 1
          and not node.GetDisplayNode().IsDisplayableInView(right.mrmlSliceNode().GetID()),
          "point placement remains functional and is displayed only on the current radiograph")
    w.deleteMarkup()
    w.openDirectory(original_source, original_project, original_reader)
    w.selectCase(original_index)

    try:
        empty = output / "empty reference folder"
        empty.mkdir()
        ref.open_directory(empty)
    except ValueError:
        pass
    else:
        raise AssertionError("An empty reference folder was accepted")
    check(ref.volume.GetID() == volume_id and ref.root == library.resolve(),
          "an unusable new folder preserves the working reference library")
    saved = ref.settings_path.read_text(encoding="utf-8")
    ref.clear()
    ref.settings_path.write_text(json.dumps({"directory": str(output / "moved-away")}), encoding="utf-8")
    ref.restored = False
    ref.restore_once()
    check(not ref.visible and bool(ref.errorLabel.text) and w.volume is not None,
          "a missing remembered folder shows a nonblocking message and leaves case reading usable")
    ref.settings_path.write_text(saved, encoding="utf-8")
    ref.restored = False
    ref.restore_once()
    check(all(file_sha256(path) == digest for path, digest in hashes.items()),
          "reading, navigating and adjusting references leaves every source file unchanged")
    return {"synthetic_only": True, "reference_count": len(ref.images), "folder_picker_visits": len(visits)}
