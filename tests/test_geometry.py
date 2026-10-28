"""Known physical dimensions, rotation invariance and incomplete geometry."""
import math
from pathlib import Path
import sys
import unittest
import uuid

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "PneumoconiosisAnnotator"))
from PneumoconiosisLib.core import validate_spatial
from PneumoconiosisLib.geometry import circle_contour, measure, measurement_scale, measurement_text


class GeometryTests(unittest.TestCase):
    def test_dicom_spacing_order_calibration_and_missing_scale(self):
        scale = measurement_scale({"PixelSpacing": [.2, .3], "ImagerPixelSpacing": [.4, .4], "PixelSpacingCalibrationType": "FIDUCIAL"})
        value = measure("circle", [[10, 20, 0], [30, 30, 0]], {"measurement_scale": scale})
        self.assertAlmostEqual(value["diameter"], math.hypot(6, 2))
        self.assertEqual(value["calibration"], "FIDUCIAL")
        self.assertEqual(measurement_text(value), "直径 6.32 mm")
        detector = measurement_scale({"ImagerPixelSpacing": [.4, .4]})
        self.assertIn("探测器", measurement_text(measure("circle", [[0, 0], [10, 20]], {"measurement_scale": detector})))
        for data in ({}, {"PixelSpacing": [0, 1]}, {"PixelSpacing": [float("nan"), 1]}, {"PixelSpacing": [1]}):
            self.assertEqual(measurement_scale(data)["unit"], "px")

    def test_minimum_feret_width_is_rotation_and_vertex_order_invariant(self):
        # A concave 12-by-3 outline has width 3; bounding-box width changes with rotation.
        outline = np.array([[0, 0], [12, 0], [12, 3], [6, 2], [0, 3]])
        for angle in (0, .3, 1.2):
            rotation = np.array([[math.cos(angle), -math.sin(angle)], [math.sin(angle), math.cos(angle)]])
            points = outline @ rotation.T + [40, 50]
            self.assertAlmostEqual(measure("polygon", points, {})["width"], 3)
            self.assertAlmostEqual(measure("polygon", points[::-1], {})["width"], 3)

    def test_circle_contour_and_draft_measurements(self):
        ends = [[10, 20, 0], [30, 30, 0]]
        image = {"columns": 100, "rows": 100, "measurement_scale": measurement_scale({"PixelSpacing": [.2, .3]})}
        contour = np.asarray(circle_contour(ends, image))
        # The stored raster is anisotropic, but every contour point is on the same physical circle.
        physical = (contour[:, :2] - [20, 25]) * [.3, .2]
        np.testing.assert_allclose(np.linalg.norm(physical, axis=1), math.sqrt(10))
        shape = {"id": uuid.uuid4().hex, "kind": "circle", "label": "C1", "points_ijk": ends}
        value = validate_spatial([shape], image)[0]
        self.assertAlmostEqual(value["measurement"]["diameter"], math.sqrt(40))
        # Horizontal and vertical diameters are valid; coincident endpoints are drafts.
        for points in ([[10, 10, 0], [10, 20, 0]], [[10, 10, 0], [20, 10, 0]]):
            self.assertEqual(validate_spatial([dict(shape, points_ijk=points)], image, complete=True)[0]["state"], "ready")
        for points in (ends[:1], [[10, 10, 0], [10, 10, 0]]):
            draft = dict(shape, points_ijk=points)
            self.assertIsNone(validate_spatial([draft], image)[0]["measurement"])
            with self.assertRaises(ValueError):
                validate_spatial([draft], image, complete=True)

    def test_obsolete_shapes_are_rejected_instead_of_fabricating_a_circle(self):
        for kind, points in (("point", [[1, 1, 0]]), ("ellipse", [[1, 1, 0], [3, 4, 0]])):
            with self.assertRaises(ValueError):
                validate_spatial([{"id": uuid.uuid4().hex, "kind": kind, "label": "old", "points_ijk": points}],
                                 {"columns": 100, "rows": 100})
