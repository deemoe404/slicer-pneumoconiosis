"""Known physical dimensions, rotation invariance and incomplete geometry."""
import math
from pathlib import Path
import sys
import unittest
import uuid

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "PneumoconiosisAnnotator"))
from PneumoconiosisLib.core import validate_spatial
from PneumoconiosisLib.geometry import ellipse_contour, measure, measurement_scale, measurement_text


class GeometryTests(unittest.TestCase):
    def test_dicom_spacing_order_calibration_and_missing_scale(self):
        scale = measurement_scale({"PixelSpacing": [.2, .3], "ImagerPixelSpacing": [.4, .4], "PixelSpacingCalibrationType": "FIDUCIAL"})
        value = measure("ellipse", [[10, 20, 0], [30, 30, 0]], {"measurement_scale": scale})
        self.assertEqual((value["long_diameter"], value["short_diameter"]), (6, 2))
        self.assertEqual(value["calibration"], "FIDUCIAL")
        self.assertEqual(measurement_text(value), "6.00 × 2.00 mm")
        detector = measurement_scale({"ImagerPixelSpacing": [.4, .4]})
        self.assertIn("探测器", measurement_text(measure("ellipse", [[0, 0], [10, 20]], {"measurement_scale": detector})))
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

    def test_ellipse_contour_and_draft_measurements(self):
        corners = [[10, 20, 0], [30, 30, 0]]
        contour = np.asarray(ellipse_contour(corners))
        np.testing.assert_allclose(((contour[:, 0] - 20) / 10) ** 2 + ((contour[:, 1] - 25) / 5) ** 2, 1)
        image = {"columns": 100, "rows": 100}
        shape = {"id": uuid.uuid4().hex, "kind": "ellipse", "label": "E1", "points_ijk": corners}
        value = validate_spatial([shape], image)[0]
        self.assertEqual(value["measurement"]["long_diameter"], 20)
        for points in (corners[:1], [[10, 10, 0], [10, 20, 0]]):
            draft = dict(shape, points_ijk=points)
            self.assertIsNone(validate_spatial([draft], image)[0]["measurement"])
            with self.assertRaises(ValueError):
                validate_spatial([draft], image, complete=True)

    def test_point_is_rejected_instead_of_fabricating_an_ellipse(self):
        with self.assertRaises(ValueError):
            validate_spatial([{"id": uuid.uuid4().hex, "kind": "point", "label": "old", "points_ijk": [[1, 1, 0]]}],
                             {"columns": 100, "rows": 100})
