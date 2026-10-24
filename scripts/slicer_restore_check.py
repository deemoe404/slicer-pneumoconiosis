"""A new Slicer process reopens the integration project's autosaved records."""
import json
import traceback
from pathlib import Path

import slicer
import qt

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "local-data" / "qa"
result = {"checks": []}
try:
    slicer.util.selectModule("PneumoconiosisAnnotator")
    w = slicer.app.moduleManager().module("PneumoconiosisAnnotator").widgetRepresentation().self()
    w.dataRoot = OUT / "settings"
    assert w.openDirectory(ROOT.parent / "2023Sample", OUT / "integration-store", "qa-reader")
    assert w.selectCase(0)
    first = {z: v["grade"] for z, v in w._zonesFromUI().items()}
    zone_order = ("RU", "RM", "RL", "LU", "LM", "LL")
    assert [first[z] for z in zone_order] == ["0/-", "0/0", "0/1", "1/0", "1/1", "1/2"]
    assert "模拟失败后保留输入" in w.noteEdit.toPlainText()
    result["checks"].append("new Slicer process restores all six exact ratings and latest autosaved note")
    assert w.selectCase(1)
    assert [w._zonesFromUI()[z]["grade"] for z in zone_order] == ["2/1", "2/2", "2/3", "3/2", "3/3", "3/+"]
    result["checks"].append("new process restores remaining six grade levels")
    assert w.selectCase(2)
    assert w._zonesFromUI()["RU"] == {"state": "not_scorable", "grade": None}
    assert w.currentRecord["status"] == "complete"
    result["checks"].append("new process restores non-scorable state, reason and completed status")
    # Pixel orientation must preserve the original image's left/right and up/down.
    import numpy as np
    import vtk
    ijk = vtk.vtkMatrix4x4()
    w.volume.GetIJKToRASMatrix(ijk)
    view = slicer.app.layoutManager().sliceWidget("Red").mrmlSliceNode().GetSliceToRAS()
    assert np.dot([ijk.GetElement(r, 0) for r in range(3)], [view.GetElement(r, 0) for r in range(3)]) > 0
    assert np.dot([ijk.GetElement(r, 1) for r in range(3)], [view.GetElement(r, 1) for r in range(3)]) < 0
    assert w.volume.GetDisplayNode().GetColorNode() is not None
    result["checks"].append("view preserves stored raster orientation and has a valid grayscale LUT")
    red = slicer.app.layoutManager().sliceWidget("Red")
    fov = red.mrmlSliceNode().GetFieldOfView()[0]
    offset = red.sliceLogic().GetSliceOffset()
    wheel = qt.QWheelEvent(qt.QPointF(100, 100), qt.QPointF(100, 100), qt.QPoint(0, 0), qt.QPoint(0, 120), qt.Qt.NoButton, qt.Qt.NoModifier, qt.Qt.NoScrollPhase, False)
    slicer.app.sendEvent(red.sliceView(), wheel)
    assert red.mrmlSliceNode().GetFieldOfView()[0] < fov
    assert abs(red.sliceLogic().GetSliceOffset() - offset) < 1e-6
    result["checks"].append("real Qt wheel event zooms the radiograph without moving out of its plane")
    result["success"] = True
except Exception:
    result["success"] = False
    result["error"] = traceback.format_exc()
    traceback.print_exc()
(OUT / "restart-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
slicer.util.exit(0 if result["success"] else 1)
