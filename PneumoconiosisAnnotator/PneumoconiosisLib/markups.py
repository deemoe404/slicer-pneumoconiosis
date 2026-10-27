"""Native Slicer Markups, stored in the source radiograph's pixel coordinates."""
from __future__ import annotations

import uuid

import slicer
import vtk


class ImageMarkups:
    def __init__(self, changed, selection_changed):
        self.changed = changed
        self.selection_changed = selection_changed
        self.nodes = {}
        self.observers = {}
        self.volume = None
        self.selected_id = None
        self.placing_id = None
        self.hovered_id = None
        self.hoverStyle = None
        self.updating = False
        self.last_snapshot = []
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

    def clear(self):
        self.highlight(None)
        self.updating = True
        try:
            self.finish()
            for identifier, node in self.nodes.items():
                for observer in self.observers[identifier]:
                    node.RemoveObserver(observer)
                slicer.mrmlScene.RemoveNode(node)
            self.nodes.clear()
            self.observers.clear()
            self.selected_id = None
            self.volume = None
            self.last_snapshot = []
        finally:
            self.updating = False

    def restore(self, volume, records):
        self.clear()
        self.updating = True
        self.volume = volume
        try:
            matrix = vtk.vtkMatrix4x4()
            volume.GetIJKToRASMatrix(matrix)
            for record in records:
                node = self._create_node(record["id"], record["kind"], record["label"])
                for point in record["points_ijk"]:
                    world = matrix.MultiplyPoint(list(point) + [1])
                    node.AddControlPointWorld(vtk.vtkVector3d(*world[:3]))
                self._set_point_label(node)
            self.last_snapshot = self.capture()
        finally:
            self.updating = False
        self.select(next(iter(self.nodes), None))

    def _create_node(self, identifier, kind, label):
        class_name = "vtkMRMLMarkupsFiducialNode" if kind == "point" else "vtkMRMLMarkupsClosedCurveNode"
        node = slicer.mrmlScene.AddNewNodeByClass(class_name, label)
        node.SetAttribute("PneumoconiosisAnnotationID", identifier)
        node.SetAttribute("PneumoconiosisKind", kind)
        node.SetAttribute("PneumoconiosisCaseID", self.volume.GetName())
        node.SetName(label)
        node.CreateDefaultDisplayNodes()
        if kind == "point":
            node.SetMaximumNumberOfControlPoints(1)
        else:
            node.SetCurveTypeToLinear()
        for index in range(node.GetNumberOfMeasurements()):
            node.GetNthMeasurement(index).SetEnabled(False)
        display = node.GetDisplayNode()
        display.AddViewNodeID(slicer.app.layoutManager().sliceWidget("Red").mrmlSliceNode().GetID())
        display.SetSelectedColor(*( (0.35, 0.85, 1.0) if kind == "point" else (1.0, 0.78, 0.25) ))
        display.SetColor(*display.GetSelectedColor())
        display.SetActiveColor(1.0, 0.35, 0.4)
        display.SetGlyphScale(1.5)
        display.SetTextScale(1.7)
        display.SetPointLabelsVisibility(kind == "point")
        display.SetPropertiesLabelVisibility(kind == "polygon")
        display.SetFillOpacity(0.08)
        display.SetLineThickness(0.2)
        self.nodes[identifier] = node
        self.observers[identifier] = [node.AddObserver(event, self._node_changed) for event in (
            node.PointModifiedEvent, node.PointPositionDefinedEvent, node.PointRemovedEvent, vtk.vtkCommand.ModifiedEvent)]
        return node

    def _set_point_label(self, node):
        if node.GetAttribute("PneumoconiosisKind") == "point" and node.GetNumberOfControlPoints():
            node.SetNthControlPointLabel(0, node.GetName())

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
            self._set_point_label(node)
        finally:
            self.updating = False
        self.last_snapshot = snapshot
        self.selected_id = node.GetAttribute("PneumoconiosisAnnotationID")
        if self.placing_id == self.selected_id and node.GetAttribute("PneumoconiosisKind") == "point" and node.GetNumberOfDefinedControlPoints() == 1:
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

    @staticmethod
    def _applyDisplayStyle(display, style):
        if display is None:
            return
        token = display.StartModify()
        try:
            for name, values in style.items():
                getattr(display, "Set" + name)(*values)
        finally:
            display.EndModify(token)

    def highlight(self, identifier):
        """Temporarily emphasize a list-hovered markup without selecting or editing it."""
        if identifier not in self.nodes:
            identifier = None
        if identifier == self.hovered_id:
            return
        updating = self.updating
        self.updating = True
        try:
            previous = self.nodes.get(self.hovered_id)
            if previous is not None and self.hoverStyle is not None:
                self._applyDisplayStyle(previous.GetDisplayNode(), self.hoverStyle)
            self.hovered_id = None
            self.hoverStyle = None
            node = self.nodes.get(identifier)
            display = node.GetDisplayNode() if node is not None else None
            if display is None:
                return
            self.hoverStyle = {
                "Color": display.GetColor(), "SelectedColor": display.GetSelectedColor(),
                "ActiveColor": display.GetActiveColor(), "GlyphScale": (display.GetGlyphScale(),),
                "TextScale": (display.GetTextScale(),), "LineThickness": (display.GetLineThickness(),),
                "FillOpacity": (display.GetFillOpacity(),),
            }
            self.hovered_id = identifier
            color = (1.0, 0.25, 0.45)
            self._applyDisplayStyle(display, {
                "Color": color, "SelectedColor": color, "ActiveColor": color,
                "GlyphScale": (display.GetGlyphScale() * 1.8,), "TextScale": (display.GetTextScale() * 1.2,),
                "LineThickness": (display.GetLineThickness() * 3,), "FillOpacity": (max(.20, display.GetFillOpacity()),),
            })
        finally:
            self.updating = updating

    def start(self, kind):
        self.finish()
        prefix = "P" if kind == "point" else "R"
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
        self.interaction.SetPlaceModePersistence(node.GetAttribute("PneumoconiosisKind") == "polygon")
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
        if identifier == self.hovered_id:
            self.highlight(None)
        self.finish()
        if identifier not in self.nodes:
            return
        node = self.nodes.pop(identifier)
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
