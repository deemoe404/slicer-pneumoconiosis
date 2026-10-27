"""Open the research reader without modifying the user's ordinary Slicer settings."""
import json
import logging
import os
from pathlib import Path

import qt
import slicer

ROOT = Path(__file__).resolve().parents[1]


def start():
    try:
        slicer.util.selectModule("PneumoconiosisAnnotator")
        widget = slicer.app.moduleManager().module("PneumoconiosisAnnotator").widgetRepresentation().self()
        data_root = widget.dataRoot
        slicer.util.mainWindow().setWindowTitle("尘肺胸片标注 · 3D Slicer")
        slicer.util.mainWindow().resize(1400, 950)
        slicer.util.setPythonConsoleVisible(False)
        slicer.util.findChild(slicer.util.mainWindow(), "PanelDockWidget").setMinimumWidth(370)
        last_path = data_root / "last-project.json"
        if last_path.exists():
            last = json.loads(last_path.read_text(encoding="utf-8"))
            widget.openDirectory(last["source"], projectDirectory=last["project"])
        else:
            sample = ROOT.parent / "2023Sample"
            if sample.exists():
                widget.openDirectory(sample)
        status = {"ready": widget.currentIndex >= 0, "case_count": len(widget.cases),
                  "slicer": slicer.app.applicationVersion, "current_reader": widget.currentReader,
                  "spatial_tools_ready": widget.pointButton.isEnabled() and widget.regionButton.isEnabled(),
                  "quality_control_ready": widget.features.qualityWidget.isEnabled(),
                  "audio_control_ready": widget.features.recordButton.isEnabled(),
                  "partition_state": widget.features.partition.state["state"] if widget.features.partition.state else None,
                  "visible_zone_count": widget.features.partition.overlay.GetSegmentation().GetNumberOfSegments() if widget.features.partition.overlay else 0}
        data_root.mkdir(parents=True, exist_ok=True)
        (data_root / "startup-status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        if os.environ.get("PNEUMO_CAPTURE_PREVIEW") == "1":
            def preview():
                widget.fitImage()
                slicer.util.mainWindow().grab().save(str(data_root / "launch-preview.png"))
            qt.QTimer.singleShot(800, preview)
    except Exception as exc:
        logging.exception("Cannot initialize pneumoconiosis reader")
        slicer.util.errorDisplay("尘肺标注模块打开失败：" + str(exc))


qt.QTimer.singleShot(0, start)
