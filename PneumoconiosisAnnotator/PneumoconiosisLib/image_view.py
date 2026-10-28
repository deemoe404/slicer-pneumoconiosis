"""Select and orient a stored radiograph in one native slice view."""
import numpy as np
import slicer
import vtk


def set_background(volume, name="Red"):
    view = slicer.app.layoutManager().sliceWidget(name)
    composite = view.mrmlSliceCompositeNode()
    composite.SetLinkedControl(False)
    composite.SetHotLinkedControl(False)
    composite.SetBackgroundVolumeID(volume.GetID() if volume is not None else None)
    composite.SetForegroundVolumeID(None)
    composite.SetLabelVolumeID(None)


def orient_image(volume, name="Red"):
    """Stored columns run left-to-right and stored rows top-to-bottom."""
    matrix = vtk.vtkMatrix4x4()
    volume.GetIJKToRASMatrix(matrix)
    x = np.array([matrix.GetElement(row, 0) for row in range(3)])
    y = -np.array([matrix.GetElement(row, 1) for row in range(3)])
    x /= np.linalg.norm(x)
    y /= np.linalg.norm(y)
    center = matrix.MultiplyPoint([(size - 1) / 2 for size in volume.GetImageData().GetDimensions()] + [1])
    pose = vtk.vtkMatrix4x4()
    pose.Identity()
    for row in range(3):
        for column, vector in enumerate((x, y, np.cross(x, y))):
            pose.SetElement(row, column, float(vector[row]))
        pose.SetElement(row, 3, center[row])
    view = slicer.app.layoutManager().sliceWidget(name)
    view.mrmlSliceNode().GetSliceToRAS().DeepCopy(pose)
    view.mrmlSliceNode().UpdateMatrices()
    view.sliceLogic().FitSliceToAll()


def remove_volume(volume):
    if volume is None or volume.GetScene() is None:
        return
    hierarchy = slicer.vtkMRMLSubjectHierarchyNode.GetSubjectHierarchyNode(slicer.mrmlScene)
    item = hierarchy.GetItemByDataNode(volume)
    if item:
        hierarchy.RemoveItem(item)
    else:
        slicer.mrmlScene.RemoveNode(volume)
