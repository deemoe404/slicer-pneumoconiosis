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


def ellipse_issue(points):
    if len(points) != 2:
        return "椭圆需要点选外接框的两个对角。"
    if abs(points[0][0] - points[1][0]) < 1e-6 or abs(points[0][1] - points[1][1]) < 1e-6:
        return "椭圆的长径和短径都必须大于零。"
    return None


def ellipse_contour(points, count=128):
    """An axis-aligned ellipse defined by two opposite bounding-box corners."""
    xy = np.asarray(points, dtype=float)[:, :2]
    center, radius = xy.mean(axis=0), np.abs(xy[1] - xy[0]) / 2
    angle = np.linspace(0, 2 * np.pi, count, endpoint=False)
    return np.column_stack((center[0] + radius[0] * np.cos(angle),
                            center[1] + radius[1] * np.sin(angle), np.zeros(count))).tolist()


def measure(kind, points, image):
    scale = image.get("measurement_scale") or measurement_scale({})
    xy = np.asarray(points, dtype=float)[:, :2] * scale["spacing"]
    if kind == "ellipse":
        long, short = sorted(np.abs(xy[1] - xy[0]), reverse=True)
        return {**scale, "method": "ellipse_axes", "long_diameter": float(long), "short_diameter": float(short)}
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
    if value["method"] == "ellipse_axes":
        return f"{value['long_diameter']:.2f} × {value['short_diameter']:.2f} {unit}"
    return f"估计宽度 {value['width']:.2f} {unit}"
