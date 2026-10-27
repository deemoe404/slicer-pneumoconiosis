"""Restore spatial QA annotations in a fresh Slicer process and verify image association."""
import json
import traceback
from pathlib import Path

import slicer
import vtk

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "local-data" / "qa"
report = {"checks": []}
try:
    previous = json.loads((OUT / "spatial-result.json").read_text())
    assert previous["success"]
    slicer.util.selectModule("PneumoconiosisAnnotator")
    w = slicer.app.moduleManager().module("PneumoconiosisAnnotator").widgetRepresentation().self()
    w.dataRoot = OUT / "spatial-settings"
    assert w.openDirectory(ROOT.parent / "2023Sample", OUT / "spatial-store", "reader-01")
    assert w.selectCase(0)
    expected = previous["expected"]
    assert w.currentRecord["spatial_annotations"] == expected
    report["checks"].append("fresh Slicer process restores exact annotation IDs, labels, states and pixel coordinates")
    matrix = vtk.vtkMatrix4x4()
    w.volume.GetIJKToRASMatrix(matrix)
    for annotation in expected:
        node = w.markups.nodes[annotation["id"]]
        assert node.GetAttribute("PneumoconiosisCaseID") == w.cases[0]["case_id"]
        for index, point in enumerate(annotation["points_ijk"]):
            actual = [0.0] * 3
            node.GetNthControlPointPositionWorld(index, actual)
            target = matrix.MultiplyPoint(point + [1])
            assert all(abs(a - b) < 1e-7 for a, b in zip(actual, target))
    report["checks"].append("restored native points and polygon align with the correct DICOM pixels in world coordinates")
    assert w.selectCase(1) and not w.markups.nodes
    assert w.selectCase(0) and len(w.markups.nodes) == 2
    report["checks"].append("fresh process switching clears and restores only the selected image's spatial nodes")
    polygon = next(a for a in expected if a["kind"] == "polygon")
    w.markups.select(polygon["id"])
    assert w.markupNameEdit.text == polygon["label"]
    node = w.markups.nodes[polygon["id"]]
    vertices = []
    for index in range(node.GetNumberOfDefinedControlPoints()):
        world = [0.0] * 3
        node.GetNthControlPointPositionWorld(index, world)
        vertices.append(world)
    edges = list(zip(vertices, vertices[1:] + vertices[:1]))
    curve = node.GetCurvePointsWorld()
    assert curve.GetNumberOfPoints() >= len(vertices)
    assert all(min(vtk.vtkLine.DistanceToLine(curve.GetPoint(i), a, b) for a, b in edges) < 1e-8
               for i in range(curve.GetNumberOfPoints()))
    report["checks"].append("region remains a named editable polygon with straight edges")
    # Removing a node through Slicer's own scene/context operations is also persisted.
    node = w.markups.nodes[polygon["id"]]
    slicer.mrmlScene.RemoveNode(node)
    assert len(w.markups.nodes) == 1 and w.flushSave()
    assert len(w.currentRecord["spatial_annotations"]) == 1
    report["checks"].append("native scene deletion cannot resurrect a removed region on autosave")
    # Restore the canonical QA fixture, including its audit trail, for later inspection.
    w.store.save(w.cases[0]["case_id"], "reader-01", w._zonesFromUI(), w.noteEdit.toPlainText(), spatial_annotations=expected)
    w.currentRecord = w.store.get(w.cases[0]["case_id"], "reader-01")
    w._restoreSpatial()
    report["success"] = True
except Exception:
    report["success"] = False
    report["error"] = traceback.format_exc()
    traceback.print_exc()
(OUT / "spatial-restart-result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
slicer.util.exit(0 if report["success"] else 1)
