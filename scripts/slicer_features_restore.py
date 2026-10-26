"""Fresh-process restoration of quality, actual recordings and clinician-corrected anatomy."""
import copy
import json
import os
from pathlib import Path
import time
import traceback

import numpy as np
import qt
import slicer
import vtk

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "local-data" / "qa"
report = {"checks": []}


def check(value, text):
    assert value, text
    report["checks"].append(text)


def events(seconds=.6):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        slicer.app.processEvents()
        time.sleep(.01)


def put_record(record):
    return w.store.save(record["case_id"], record["reader_id"], record["zones"], record["note"], record["status"], record["display"],
                        record["spatial_annotations"], record["quality"], record["partition"], record["audio"])


try:
    previous = json.loads((OUT / "features-result.json").read_text())
    assert previous["success"]
    slicer.util.selectModule("PneumoconiosisAnnotator")
    w = slicer.app.moduleManager().module("PneumoconiosisAnnotator").widgetRepresentation().self()
    w.dataRoot = OUT / "features-settings"
    check(w.openDirectory(ROOT.parent / "2023Sample", previous["project_directory"], "qa-features"), "fresh Slicer process reopens the isolated feature project")
    w.selectCase(0)
    f = w.features
    expected = previous["expected_first"]
    check(w.currentRecord["quality"] == expected["quality"] and len(f.audio) == 1 and f.audio == expected["audio"], "quality grade, defect reason and the real microphone recording restore exactly")
    check(w.currentRecord["partition"] == expected["partition"], "corrected lung-mask hashes, landmarks, model provenance and clinician confirmation restore exactly")
    right, left = w.store.assets.lungs(expected["partition"]["masks"])
    actual_right, actual_left = f.partition.masks()
    check(np.array_equal(right, actual_right) and np.array_equal(left, actual_left), "native editable lung masks match the stored original-resolution pixels after restart")
    matrix = vtk.vtkMatrix4x4()
    w.volume.GetIJKToRASMatrix(matrix)
    from PneumoconiosisLib.partition_view import ANCHORS
    for index, (side, name, _) in enumerate(ANCHORS):
        point = expected["partition"]["landmarks"][side][name]
        target = matrix.MultiplyPoint(point + [1])
        world = [0.0] * 3
        f.partition.anchors.GetNthControlPointPositionWorld(index, world)
        assert max(abs(a - b) for a, b in zip(target[:3], world)) < 1e-6
    check(True, "restored apex/dome control points remain aligned to the correct source DICOM pixels")
    node = f.partition.anchors
    point = expected["partition"]["landmarks"]["R"]["dome"]
    world = matrix.MultiplyPoint([point[0], point[1] + 20, 0, 1])
    node.SetNthControlPointPositionWorld(1, *world[:3])
    events(.8)
    check(w.currentRecord["partition"]["state"] == "edited" and w.currentRecord["partition"]["confirmed_at"] is None,
          "editing a previously confirmed partition clears its confirmation and autosaves the new geometry")
    check(f.confirm_partition(), "clinician can confirm the corrected partition again")
    # Restore the canonical QA fixture after testing state transitions.
    w.currentRecord = put_record(expected)
    f.restore()

    w.selectCase(1)
    original = copy.deepcopy(w.currentRecord)
    f.qualityChoices.buttons[1].click()
    for choices in w.gradeChoices.values():
        choices.buttons["0/0"].click()
    check(not w.completeCase() and w.currentIndex == 1, "completion rejects an unconfirmed automatic partition even when quality and all six ratings are filled")
    check(f.confirm_partition() and w.completeCase() and w.currentIndex == 2, "confirmed partition, quality and six ratings can complete and advance together")
    put_record(original)
    check(not f.audio and "本地录音" in f.audioLabel.text, "a different case's audio controls report its own recording state")
    lock = qt.QLockFile(str(w.store.directory / "reader.lock"))
    lock.setStaleLockTime(0)
    os.utime(w.store.directory / "reader.lock", (1, 1))
    check(not lock.tryLock(10), "a second writer cannot obtain the active project's edit lock")
    w.selectCase(0)
    check(w.currentRecord["partition"] == expected["partition"] and w.currentRecord["audio"] == expected["audio"], "round-trip navigation preserves the original reader's corrected geometry and audio")
    report["success"] = True
except Exception:
    report["success"] = False
    report["error"] = traceback.format_exc()
    traceback.print_exc()
(OUT / "features-restart-result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
slicer.util.exit(0 if report["success"] else 1)
