"""Validate Qt-owned OpenGL with an actual Slicer view capture."""
import re

import numpy as np
import vtk
from vtk.util.numpy_support import vtk_to_numpy
from ScreenCapture import ScreenCaptureLogic


def capture_rendered_view(view, filename):
    view.forceRender()
    window = view.renderWindow()
    window.MakeCurrent()
    capabilities = window.ReportCapabilities()
    version = re.search(r"OpenGL version string:\s*(\d+)\.(\d+)", capabilities)
    if window.GetNeverRendered() or not window.IsCurrent() or not version or tuple(map(int, version.groups())) < (3, 2):
        raise AssertionError("No rendered Qt OpenGL 3.2+ context: " + capabilities)
    # Generic render windows delegate SupportsOpenGL to an optional observer.
    # Its default 0 is not a capability test for Slicer's Qt-owned context.
    ScreenCaptureLogic().captureImageFromView(view, str(filename))
    reader = vtk.vtkPNGReader()
    reader.SetFileName(str(filename))
    reader.Update()
    image = reader.GetOutput()
    pixels = vtk_to_numpy(image.GetPointData().GetScalars()).astype(np.float32)
    if min(image.GetDimensions()[:2]) < 100 or pixels.ndim != 2 or pixels.shape[1] < 3:
        raise AssertionError("The captured view is empty or too small")
    rgb = pixels[:, :3]
    gray = rgb[np.ptp(rgb, axis=1) < 4].mean(axis=1)
    if len(gray) < 1000 or np.diff(np.percentile(gray, [5, 95]))[0] < 40:
        raise AssertionError("The captured radiograph has insufficient grayscale variation")
    return {"renderer": capabilities, "render_window": window.GetClassName(),
            "opengl_version": list(map(int, version.groups())),
            "capture_dimensions": list(image.GetDimensions()), "gray_pixel_count": len(gray)}
