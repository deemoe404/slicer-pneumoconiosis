"""Open the research reader without modifying the user's ordinary Slicer settings."""
import json
import logging
import os
from pathlib import Path
import sys
import traceback

import qt
import slicer

ROOT = Path(__file__).resolve().parents[1]


def start():
    window = slicer.util.mainWindow()
    if window.property("PneumoStartupState") in ("starting", "initialized", "failed"):
        return
    window.setProperty("PneumoStartupState", "starting")
    data_root = Path(os.environ.get("PNEUMO_DATA_ROOT", ROOT / "local-data"))
    try:
        module_dir = str(ROOT / "PneumoconiosisAnnotator")
        if module_dir not in sys.path:
            sys.path.insert(0, module_dir)
        from PneumoconiosisLib.language import initialize
        initialize()
        slicer.util.selectModule("PneumoconiosisAnnotator")
        widget = slicer.app.moduleManager().module("PneumoconiosisAnnotator").widgetRepresentation().self()
        data_root = widget.dataRoot
        window.setWindowTitle("尘肺胸片标注 · 3D Slicer")
        window.resize(1400, 950)
        console = window.pythonConsole()
        if console is not None and console.parent() is not None:
            slicer.util.setPythonConsoleVisible(False)
        slicer.util.findChild(window, "PanelDockWidget").setMinimumWidth(370)
        last_path = data_root / "last-project.json"
        if last_path.exists():
            last = json.loads(last_path.read_text(encoding="utf-8"))
            if Path(last["source"]).is_dir():
                widget.openDirectory(last["source"], projectDirectory=last["project"])
            else:
                logging.warning("Previous chest-radiograph directory is unavailable; select its new location.")
        else:
            from PneumoconiosisLib.runtime import packaged
            sample = ROOT.parent / "2023Sample"
            if not packaged(ROOT) and sample.exists():
                widget.openDirectory(sample)
        status = {"initialized": True, "process_id": os.getpid(),
                  "interface_language": "zh_CN", "selected_module": slicer.util.selectedModule(),
                  "ready": widget.currentIndex >= 0, "case_count": len(widget.cases),
                  "slicer": slicer.app.applicationVersion, "current_reader": widget.currentReader,
                  "spatial_tools_ready": widget.pointButton.isEnabled() and widget.regionButton.isEnabled(),
                  "quality_control_ready": widget.features.qualityWidget.isEnabled(),
                  "audio_control_ready": widget.features.recordButton.isEnabled(),
                  "partition_state": widget.features.partition.state["state"] if widget.features.partition.state else None,
                  "visible_zone_count": widget.features.partition.overlay.GetSegmentation().GetNumberOfSegments() if widget.features.partition.overlay else 0}
        data_root.mkdir(parents=True, exist_ok=True)
        (data_root / "startup-status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        window.setProperty("PneumoStartupState", "initialized")
        if os.environ.get("PNEUMO_CAPTURE_PREVIEW") == "1":
            def preview():
                widget.fitImage()
                slicer.util.mainWindow().grab().save(str(data_root / "launch-preview.png"))
            qt.QTimer.singleShot(800, preview)
    except Exception as exc:
        window.setProperty("PneumoStartupState", "failed")
        logging.exception("Cannot initialize pneumoconiosis reader")
        data_root.mkdir(parents=True, exist_ok=True)
        (data_root / "startup-status.json").write_text(
            json.dumps({"initialized": False, "process_id": os.getpid(), "error": traceback.format_exc()},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        slicer.util.errorDisplay("尘肺标注模块打开失败：" + str(exc))


if slicer.util.mainWindow().isVisible():
    qt.QTimer.singleShot(0, start)
else:
    # Let all native startupCompleted handlers finish before customizing the UI.
    slicer.app.connect("startupCompleted()", lambda: qt.QTimer.singleShot(0, start))
