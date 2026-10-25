"""Editable native Slicer lung masks, six-zone overlays and apex/dome control points."""
from __future__ import annotations

import copy

import numpy as np
import qt
import slicer
import vtk

from .partition import ZONE_LABELS, zone_map

COLORS = ((.24, .75, 1.0), (.3, .85, .65), (.95, .78, .28),
          (.62, .55, 1.0), (.95, .5, .5), (.95, .65, .3))
ANCHORS = (("R", "apex", "R-A"), ("R", "dome", "R-D"), ("L", "apex", "L-A"), ("L", "dome", "L-D"))


class ImagePartition:
    def __init__(self, changed, error):
        self.changed, self.error = changed, error
        self.volume = self.assets = self.state = None
        self.lungs = self.overlay = self.anchors = None
        self.dialog = self.editor = self.editor_node = None
        self.observers = []
        self.updating = False
        self.masks_dirty = False
        self.visible = True
        self.timer = qt.QTimer()
        self.timer.setSingleShot(True)
        self.timer.setInterval(250)
        self.timer.connect("timeout()", self.rebuild)

    def clear(self):
        self.updating = True
        self.timer.stop()
        self.close_editor()
        for node, tag in self.observers:
            node.RemoveObserver(tag)
        self.observers = []
        for node in (self.lungs, self.overlay, self.anchors, self.editor_node):
            if node is not None and node.GetScene() is not None:
                slicer.mrmlScene.RemoveNode(node)
        self.volume = self.assets = self.state = None
        self.lungs = self.overlay = self.anchors = self.editor_node = None
        self.masks_dirty = False
        self.updating = False

    def restore(self, volume, state, assets):
        self.clear()
        if state is None:
            return
        self.updating = True
        self.volume, self.assets, self.state = volume, assets, copy.deepcopy(state)
        try:
            self.lungs = self._segmentation("肺野 · " + volume.GetName())
            segmentation = self.lungs.GetSegmentation()
            for identifier, name, color in (("R", "患者右肺", COLORS[0]), ("L", "患者左肺", COLORS[3])):
                segmentation.AddEmptySegment(identifier, name, color)
            for identifier, mask in zip(("R", "L"), assets.lungs(state["masks"])):
                slicer.util.updateSegmentBinaryLabelmapFromArray(mask[None].astype(np.uint8), self.lungs, identifier, volume)
            self.lungs.GetDisplayNode().SetVisibility(False)
            self.overlay = self._segmentation("六肺区 · " + volume.GetName())
            for (label, name), color in zip(ZONE_LABELS.items(), COLORS):
                self.overlay.GetSegmentation().AddEmptySegment(name, name, color)
            self.anchors = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsFiducialNode", "肺尖与膈顶")
            self.anchors.SetMaximumNumberOfControlPoints(4)
            self.anchors.SetRequiredNumberOfControlPoints(4)
            self.anchors.CreateDefaultDisplayNodes()
            self.anchors.SetAttribute("PneumoconiosisCaseID", volume.GetName())
            matrix = vtk.vtkMatrix4x4()
            volume.GetIJKToRASMatrix(matrix)
            for side, name, label in ANCHORS:
                world = matrix.MultiplyPoint(list(state["landmarks"][side][name]) + [1])
                index = self.anchors.AddControlPointWorld(vtk.vtkVector3d(*world[:3]))
                self.anchors.SetNthControlPointLabel(index, label)
            self.anchors.SetFixedNumberOfControlPoints(True)
            display = self.anchors.GetDisplayNode()
            display.AddViewNodeID(slicer.app.layoutManager().sliceWidget("Red").mrmlSliceNode().GetID())
            display.SetSelectedColor(.95, .85, .35)
            display.SetActiveColor(1.0, .25, .25)
            display.SetGlyphScale(2.2)
            display.SetTextScale(1.7)
            self.anchors.SetLocked(True)
            self.observers = [
                (segmentation, segmentation.AddObserver(slicer.vtkSegmentation.SourceRepresentationModified, self.mask_changed)),
                (self.anchors, self.anchors.AddObserver(self.anchors.PointModifiedEvent, self.landmark_changed)),
            ]
        finally:
            self.updating = False
        self.rebuild()

    def _segmentation(self, name):
        node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSegmentationNode", name)
        node.CreateDefaultDisplayNodes()
        node.SetReferenceImageGeometryParameterFromVolumeNode(self.volume)
        node.SetAttribute("PneumoconiosisCaseID", self.volume.GetName())
        display = node.GetDisplayNode()
        display.AddViewNodeID(slicer.app.layoutManager().sliceWidget("Red").mrmlSliceNode().GetID())
        display.SetOpacity2DFill(.10)
        display.SetOpacity2DOutline(.85)
        return node

    def masks(self):
        return tuple(slicer.util.arrayFromSegmentBinaryLabelmap(self.lungs, side, self.volume)[0].astype(bool) for side in ("R", "L"))

    def landmarks(self):
        if self.anchors.GetNumberOfDefinedControlPoints() != 4:
            raise ValueError("需要保留四个肺尖 / 膈顶定位点。")
        matrix = vtk.vtkMatrix4x4()
        self.volume.GetRASToIJKMatrix(matrix)
        output = {"R": {}, "L": {}}
        for index, (side, name, _label) in enumerate(ANCHORS):
            world = [0.0] * 3
            self.anchors.GetNthControlPointPositionWorld(index, world)
            output[side][name] = [round(float(v), 6) for v in matrix.MultiplyPoint(world + [1])[:3]]
        return output

    def mask_changed(self, *_):
        if not self.updating:
            self.masks_dirty = True
            self._edited()

    def landmark_changed(self, *_):
        if not self.updating:
            self._edited()

    def _edited(self):
        self.state["state"] = "edited"
        self.state["confirmed_at"] = None
        self.state["confirmed_by"] = None
        self.changed()
        self.timer.start()

    def capture(self):
        if self.state is None:
            return None
        self.state["landmarks"] = self.landmarks()
        if self.masks_dirty:
            self.state["masks"] = self.assets.put_lungs(*self.masks())
            self.masks_dirty = False
        return copy.deepcopy(self.state)

    def rebuild(self):
        self.timer.stop()
        if self.state is None:
            return
        try:
            labels = zone_map(*self.masks(), self.landmarks())
            self.updating = True
            with slicer.util.RenderBlocker():
                for value, name in ZONE_LABELS.items():
                    slicer.util.updateSegmentBinaryLabelmapFromArray((labels == value)[None].astype(np.uint8), self.overlay, name, self.volume)
            self.overlay.GetDisplayNode().SetVisibility(self.visible)
        except Exception as exc:
            if self.overlay:
                self.overlay.GetDisplayNode().SetVisibility(False)
            self.error(str(exc))
        finally:
            self.updating = False

    def set_visible(self, visible):
        self.visible = bool(visible)
        for node in (self.overlay, self.anchors):
            if node:
                node.GetDisplayNode().SetVisibility(self.visible)

    def edit_landmarks(self, enabled):
        if self.anchors:
            self.anchors.SetLocked(not enabled)
            if enabled:
                self.anchors.GetDisplayNode().SetVisibility(True)
                slicer.modules.markups.logic().SetActiveListID(self.anchors)

    def open_editor(self):
        if self.lungs is None:
            return
        if self.dialog:
            self.dialog.show()
            self.dialog.raise_()
            return
        self.edit_landmarks(False)
        self.dialog = qt.QDialog(slicer.util.mainWindow())
        self.dialog.setWindowTitle("修正肺野 · 选择左右肺后，用画笔或橡皮调整")
        self.dialog.resize(390, 540)
        layout = qt.QVBoxLayout(self.dialog)
        hint = qt.QLabel("在列表选患者左 / 右肺，在胸片上用画笔或橡皮修正。\n关闭此窗口后，修改仍会自动保存并更新六区。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.editor_node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSegmentEditorNode")
        self.editor = slicer.qMRMLSegmentEditorWidget()
        self.editor.setMRMLSegmentEditorNode(self.editor_node)
        self.editor.setMRMLScene(slicer.mrmlScene)
        self.editor.setSegmentationNode(self.lungs)
        self.editor.setSourceVolumeNode(self.volume)
        self.editor.setEffectNameOrder(["Paint", "Erase"])
        self.editor.unorderedEffectsVisible = False
        self.editor.segmentationNodeSelectorVisible = False
        self.editor.sourceVolumeNodeSelectorVisible = False
        self.editor.maskingSectionVisible = False
        self.editor.addRemoveSegmentButtonsVisible = False
        self.editor.switchToSegmentationsButtonVisible = False
        self.editor.show3DButtonVisible = False
        self.editor.specifyGeometryButtonVisible = False
        self.editor.jumpToSelectedSegmentEnabled = False
        self.editor.undoEnabled = True
        self.editor.maximumNumberOfUndoStates = 5
        self.editor.setCurrentSegmentID("R")
        layout.addWidget(self.editor)
        done = qt.QPushButton("完成修正")
        done.connect("clicked()", self.close_editor)
        layout.addWidget(done)
        self.dialog.connect("finished(int)", self.close_editor)
        self.lungs.GetDisplayNode().SetVisibility(True)
        self.overlay.GetDisplayNode().SetVisibility(False)
        self.dialog.show()

    def close_editor(self, *_):
        dialog, self.dialog = self.dialog, None
        if self.editor:
            self.editor.setActiveEffectByName("")
            self.editor.setSegmentationNode(None)
            self.editor.setSourceVolumeNode(None)
            self.editor.setMRMLScene(None)
            self.editor = None
        if dialog:
            dialog.hide()
            dialog.deleteLater()
        if self.lungs:
            self.lungs.GetDisplayNode().SetVisibility(False)
        if self.overlay:
            self.overlay.GetDisplayNode().SetVisibility(self.visible)
        if self.editor_node is not None:
            slicer.mrmlScene.RemoveNode(self.editor_node)
            self.editor_node = None
