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
        slicer.util.mainWindow().setWindowTitle("尘肺胸片标注 · 3D Slicer")
        slicer.util.mainWindow().resize(1400, 950)
        slicer.util.setToolbarsVisible(False)
        slicer.util.setDataProbeVisible(False)
        slicer.util.setPythonConsoleVisible(False)
        slicer.util.setModulePanelTitleVisible(False)
        slicer.util.findChild(slicer.util.mainWindow(), "PanelDockWidget").setMinimumWidth(370)
        last_path = ROOT / "local-data" / "last-project.json"
        if last_path.exists():
            last = json.loads(last_path.read_text())
            widget.openDirectory(last["source"], projectDirectory=last["project"])
        else:
            sample = ROOT.parent / "2023Sample"
            if sample.exists():
                widget.openDirectory(sample)
        status = {"ready": widget.currentIndex >= 0, "case_count": len(widget.cases),
                  "slicer": slicer.app.applicationVersion, "current_reader": widget.currentReader,
                  "spatial_tools_ready": widget.pointButton.isEnabled() and widget.regionButton.isEnabled()}
        (ROOT / "local-data").mkdir(parents=True, exist_ok=True)
        (ROOT / "local-data" / "startup-status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2))
        if os.environ.get("PNEUMO_CAPTURE_PREVIEW") == "1":
            def preview():
                widget.fitImage()
                slicer.util.mainWindow().grab().save(str(ROOT / "local-data" / "launch-preview.png"))
            qt.QTimer.singleShot(800, preview)
    except Exception as exc:
        logging.exception("Cannot initialize pneumoconiosis reader")
        slicer.util.errorDisplay("尘肺标注模块打开失败：" + str(exc))


qt.QTimer.singleShot(0, start)
