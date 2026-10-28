"""Image-plane geometry and explicit, reproducible size measurements."""
import math

import numpy as np
from scipy.spatial import ConvexHull


def measurement_scale(dataset):
    for name in ("PixelSpacing", "ImagerPixelSpacing"):
        try:
            row, column = map(float, dataset.get(name, []))
            if all(math.isfinite(v) and v > 0 for v in (row, column)):
                return {"unit": "mm", "spacing": [column, row], "source": name,
                        "calibration": str(dataset.get("PixelSpacingCalibrationType", ""))}
        except (TypeError, ValueError):
            pass
    return {"unit": "px", "spacing": [1., 1.], "source": "pixels", "calibration": ""}


def circle_issue(points):
    if len(points) != 2:
        return "圆形需要点选直径的两个端点。"
    if math.dist(points[0][:2], points[1][:2]) < 1e-6:
        return "圆形直径必须大于零。"
    return None


def circle_geometry(points, image):
    """Diameter endpoints in IJK; a circle in the image's physical measurement plane."""
    xy = np.asarray(points, dtype=float)[:, :2]
    spacing = np.asarray((image.get("measurement_scale") or measurement_scale({}))["spacing"])
    radius = np.linalg.norm((xy[1] - xy[0]) * spacing) / 2
    return xy.mean(axis=0), radius / spacing


def circle_contour(points, image, count=128):
    """Raster contour for export; non-square pixels turn a physical circle into a pixel ellipse."""
    center, radius = circle_geometry(points, image)
    angle = np.linspace(0, 2 * np.pi, count, endpoint=False)
    return np.column_stack((center[0] + radius[0] * np.cos(angle),
                            center[1] + radius[1] * np.sin(angle), np.zeros(count))).tolist()


def measure(kind, points, image):
    scale = image.get("measurement_scale") or measurement_scale({})
    xy = np.asarray(points, dtype=float)[:, :2] * scale["spacing"]
    if kind == "circle":
        return {**scale, "method": "circle_diameter", "diameter": float(np.linalg.norm(xy[1] - xy[0]))}
    hull = xy[ConvexHull(xy).vertices]
    edges = np.roll(hull, -1, axis=0) - hull
    normals = np.column_stack((-edges[:, 1], edges[:, 0]))
    normals /= np.linalg.norm(normals, axis=1)[:, None]
    widths = np.ptp(hull @ normals.T, axis=0)
    return {**scale, "method": "minimum_feret", "width": float(widths.min())}


def measurement_text(value):
    if not value:
        return ""
    unit = "mm（探测器）" if value["source"] == "ImagerPixelSpacing" else value["unit"]
    if value["method"] == "circle_diameter":
        return f"直径 {value['diameter']:.2f} {unit}"
    return f"宽度 {value['width']:.2f} {unit}"
