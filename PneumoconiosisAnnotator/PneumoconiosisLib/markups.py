"""Native Slicer Markups, stored in the source radiograph's pixel coordinates."""
from __future__ import annotations

import uuid

import numpy as np
import qt
import slicer
import vtk

from .core import shape_issue
from .geometry import ellipse_contour, measure, measurement_text


class ImageMarkups(qt.QWidget):
    def __init__(self, changed, selection_changed):
        super().__init__()
        self.changed = changed
        self.selection_changed = selection_changed
        self.nodes = {}
        self.observers = {}
        self.volume = None
        self.selected_id = None
        self.placing_id = None
        self.updating = False
        self.last_snapshot = []
        self.outlines, self.labels = {}, {}
        self.image = {}
        self.view = slicer.app.layoutManager().sliceWidget("Red").sliceView()
        self.slice = slicer.app.layoutManager().sliceWidget("Red").mrmlSliceNode()
        self.view.installEventFilter(self)
        self.view_observer = self.slice.AddObserver(vtk.vtkCommand.ModifiedEvent, self._position_labels)
        self.interaction = slicer.app.applicationLogic().GetInteractionNode()
        self.interaction_observer = self.interaction.AddObserver(vtk.vtkCommand.ModifiedEvent, self._interaction_changed)

        @vtk.calldata_type(vtk.VTK_OBJECT)
        def removed(_scene, _event, node):
            if not self.updating:
                identifier = next((key for key, value in self.nodes.items() if value == node), None)
                if identifier:
                    self.remove(identifier)

        self.removed_callback = removed
        self.scene_observer = slicer.mrmlScene.AddObserver(slicer.vtkMRMLScene.NodeRemovedEvent, removed)

    def close(self):
        self.clear()
        self.interaction.RemoveObserver(self.interaction_observer)
        slicer.mrmlScene.RemoveObserver(self.scene_observer)
        self.slice.RemoveObserver(self.view_observer)
        self.view.removeEventFilter(self)

    def clear(self):
        self.updating = True
        try:
            self.finish()
            for identifier, node in self.nodes.items():
                for observer in self.observers[identifier]:
                    node.RemoveObserver(observer)
                slicer.mrmlScene.RemoveNode(node)
            self.nodes.clear()
            self.observers.clear()
            for node in self.outlines.values():
                slicer.mrmlScene.RemoveNode(node)
            for label in self.labels.values():
                label.hide()
                label.deleteLater()
            self.outlines.clear()
            self.labels.clear()
            self.selected_id = None
            self.volume = None
            self.last_snapshot = []
        finally:
            self.updating = False

    def restore(self, volume, records, image):
        self.clear()
        self.updating = True
        self.volume = volume
        self.image = image
        try:
            matrix = vtk.vtkMatrix4x4()
            volume.GetIJKToRASMatrix(matrix)
            for record in records:
                node = self._create_node(record["id"], record["kind"], record["label"])
                for point in record["points_ijk"]:
                    world = matrix.MultiplyPoint(list(point) + [1])
                    node.AddControlPointWorld(vtk.vtkVector3d(*world[:3]))
            self._update_visuals()
            self.last_snapshot = self.capture()
        finally:
            self.updating = False
        self.select(next(iter(self.nodes), None))

    def _create_node(self, identifier, kind, label):
        class_name = "vtkMRMLMarkupsFiducialNode" if kind == "ellipse" else "vtkMRMLMarkupsClosedCurveNode"
        node = slicer.mrmlScene.AddNewNodeByClass(class_name, label)
        node.SetAttribute("PneumoconiosisAnnotationID", identifier)
        node.SetAttribute("PneumoconiosisKind", kind)
        node.SetAttribute("PneumoconiosisCaseID", self.volume.GetName())
        node.SetName(label)
        node.CreateDefaultDisplayNodes()
        if kind == "ellipse":
            node.SetMaximumNumberOfControlPoints(2)
            node.SetRequiredNumberOfControlPoints(2)
        else:
            node.SetCurveTypeToLinear()
        for index in range(node.GetNumberOfMeasurements()):
            node.GetNthMeasurement(index).SetEnabled(False)
        display = node.GetDisplayNode()
        display.AddViewNodeID(slicer.app.layoutManager().sliceWidget("Red").mrmlSliceNode().GetID())
        display.SetSelectedColor(*( (0.35, 0.85, 1.0) if kind == "ellipse" else (1.0, 0.78, 0.25) ))
        display.SetColor(*display.GetSelectedColor())
        display.SetActiveColor(1.0, 0.35, 0.4)
        display.SetGlyphScale(1.5)
        display.SetTextScale(1.7)
        display.SetPointLabelsVisibility(False)
        display.SetPropertiesLabelVisibility(False)
        display.SetFillOpacity(0.08)
        display.SetLineThickness(0.2)
        self.nodes[identifier] = node
        label_widget = qt.QLabel(self.view)
        label_widget.setAttribute(qt.Qt.WA_TransparentForMouseEvents)
        color = "#72dfff" if kind == "ellipse" else "#ffcf65"
        label_widget.setStyleSheet(f"color:{color}; background:rgba(15,22,30,190); border-radius:3px; padding:2px 4px; font-size:12px;")
        self.labels[identifier] = label_widget
        if kind == "ellipse":
            outline = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsClosedCurveNode", "Ellipse outline")
            outline.SetHideFromEditors(True)
            outline.SetSaveWithScene(False)
            outline.SetLocked(True)
            outline.SetCurveTypeToLinear()
            outline.CreateDefaultDisplayNodes()
            outline.GetDisplayNode().CopyContent(display)
            outline.GetDisplayNode().SetVisibility(False)
            for index in range(outline.GetNumberOfMeasurements()):
                outline.GetNthMeasurement(index).SetEnabled(False)
            self.outlines[identifier] = outline
        self.observers[identifier] = [node.AddObserver(event, self._node_changed) for event in (
            node.PointModifiedEvent, node.PointPositionDefinedEvent, node.PointRemovedEvent, vtk.vtkCommand.ModifiedEvent)]
        return node

    def _update_visuals(self):
        matrix = vtk.vtkMatrix4x4()
        self.volume.GetIJKToRASMatrix(matrix)
        records = self.capture()
        present = {record["id"] for record in records}
        for identifier in self.nodes.keys() - present:
            self.labels[identifier].hide()
            if identifier in self.outlines:
                self.outlines[identifier].GetDisplayNode().SetVisibility(False)
        for record in records:
            identifier, points = record["id"], record["points_ijk"]
            valid = not shape_issue(record["kind"], points)
            size = measurement_text(measure(record["kind"], points, self.image)) if valid else "草稿"
            self.labels[identifier].setText(record["label"] + " · " + size)
            self.labels[identifier].adjustSize()
            if identifier in self.outlines:
                outline = self.outlines[identifier]
                outline.GetDisplayNode().SetVisibility(valid)
                if valid:
                    world = [matrix.MultiplyPoint(p + [1])[:3] for p in ellipse_contour(points)]
                    slicer.util.updateMarkupsControlPointsFromArray(outline, np.array(world), world=True)
                    state = outline.StartModify()
                    for index in range(outline.GetNumberOfControlPoints()):
                        outline.SetNthControlPointVisibility(index, False)
                    outline.EndModify(state)
        self._position_labels()

    def _position_labels(self, *_):
        if self.volume is None:
            return
        matrix = vtk.vtkMatrix4x4()
        self.volume.GetIJKToRASMatrix(matrix)
        inverse = vtk.vtkMatrix4x4()
        inverse.DeepCopy(self.slice.GetXYToRAS())
        inverse.Invert()
        ratio = self.view.devicePixelRatioF()
        for record in self.capture():
            points = record["points_ijk"]
            anchor = [max(p[0] for p in points), min(p[1] for p in points), 0, 1]
            xy = inverse.MultiplyPoint(matrix.MultiplyPoint(anchor))
            x, y = xy[0] / ratio + 8, self.view.height - xy[1] / ratio - 22
            label = self.labels[record["id"]]
            label.setVisible(0 <= x < self.view.width and 0 <= y < self.view.height)
            label.move(round(min(x, self.view.width - label.width)), round(max(0, y)))
            label.raise_()

    def eventFilter(self, obj, event):
        if obj == self.view and event.type() == qt.QEvent.Resize:
            self._position_labels()
        return False

    def capture(self):
        if self.volume is None:
            return []
        matrix = vtk.vtkMatrix4x4()
        self.volume.GetRASToIJKMatrix(matrix)
        result = []
        for identifier, node in self.nodes.items():
            if node.GetScene() is None:
                continue
            points = []
            for index in range(node.GetNumberOfControlPoints()):
                if node.GetNthControlPointPositionStatus(index) != slicer.vtkMRMLMarkupsNode.PositionDefined:
                    continue  # The cursor's preview point is not an annotation.
                world = [0.0, 0.0, 0.0]
                node.GetNthControlPointPositionWorld(index, world)
                ijk = matrix.MultiplyPoint(world + [1])
                points.append([round(float(v), 6) for v in ijk[:3]])
            if points:
                result.append({"id": identifier, "kind": node.GetAttribute("PneumoconiosisKind"),
                               "label": node.GetName(), "points_ijk": points})
        return result

    def _node_changed(self, node, _event):
        if self.updating:
            return
        snapshot = self.capture()
        if snapshot == self.last_snapshot:
            return
        self.updating = True
        try:
            self._update_visuals()
        finally:
            self.updating = False
        self.last_snapshot = snapshot
        self.selected_id = node.GetAttribute("PneumoconiosisAnnotationID")
        if self.placing_id == self.selected_id and node.GetAttribute("PneumoconiosisKind") == "ellipse" and node.GetNumberOfDefinedControlPoints() == 2:
            self.finish()
        self.changed(snapshot)
        self.selection_changed()

    def _interaction_changed(self, *_):
        if self.placing_id and self.interaction.GetCurrentInteractionMode() != self.interaction.Place:
            self.finish()

    def select(self, identifier):
        if self.placing_id and identifier != self.placing_id:
            self.finish()
        self.selected_id = identifier
        if identifier in self.nodes:
            slicer.modules.markups.logic().SetActiveListID(self.nodes[identifier])
        if not self.updating:
            self.selection_changed()

    def start(self, kind):
        self.finish()
        prefix = "E" if kind == "ellipse" else "R"
        names = {node.GetName() for node in self.nodes.values()}
        number = 1
        while prefix + str(number) in names:
            number += 1
        identifier = uuid.uuid4().hex
        self._create_node(identifier, kind, prefix + str(number))
        self.select(identifier)
        self.continue_placing()
        return self.nodes[identifier]

    def continue_placing(self):
        node = self.nodes.get(self.selected_id)
        if node is None:
            return
        slicer.modules.markups.logic().SetActiveListID(node)
        slicer.app.applicationLogic().GetSelectionNode().SetReferenceActivePlaceNodeClassName(node.GetClassName())
        self.interaction.SetPlaceModePersistence(1)
        self.interaction.SetCurrentInteractionMode(self.interaction.Place)
        self.placing_id = self.selected_id
        self.selection_changed()

    def finish(self):
        if self.placing_id:
            identifier = self.placing_id
            self.placing_id = None
            self.interaction.SwitchToViewTransformMode()
            self.interaction.SetPlaceModePersistence(0)
            node = self.nodes.get(identifier)
            if node is not None and node.GetNumberOfDefinedControlPoints() == 0:
                self.remove(identifier)
            if not self.updating:
                self.selection_changed()

    def rename(self, label):
        node = self.nodes.get(self.selected_id)
        if node is not None:
            node.SetName(label)

    def remove(self, identifier=None):
        identifier = identifier or self.selected_id
        if identifier not in self.nodes:
            return
        self.finish()
        if identifier not in self.nodes:
            return
        node = self.nodes.pop(identifier)
        label = self.labels.pop(identifier)
        label.hide()
        label.deleteLater()
        outline = self.outlines.pop(identifier, None)
        if outline is not None:
            slicer.mrmlScene.RemoveNode(outline)
        for observer in self.observers.pop(identifier):
            node.RemoveObserver(observer)
        if node.GetScene():
            slicer.mrmlScene.RemoveNode(node)
        self.selected_id = next(reversed(self.nodes), None)
        snapshot = self.capture()
        if snapshot != self.last_snapshot:
            self.last_snapshot = snapshot
            self.changed(snapshot)
        self.select(self.selected_id)
