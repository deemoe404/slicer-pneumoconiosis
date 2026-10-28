"""Headless integration using only the committed ImageGen fixture and real workers.

This checks backend portability, not Slicer GUI, microphone capture or clinical
accuracy. Generated DICOM, logs, SQLite and exports stay under local-data/qa.
"""
from __future__ import annotations

import argparse
import copy
import csv
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import struct
import subprocess
import sys
import time
import traceback
import uuid

import numpy as np
from PIL import Image
import pydicom
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import DigitalXRayImageStorageForPresentation, ExplicitVRLittleEndian, generate_uid
import sounddevice

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "PneumoconiosisAnnotator"))
from PneumoconiosisLib.assets import resolve_asset
from PneumoconiosisLib.audio_files import WaveRecording
from PneumoconiosisLib.core import AnnotationStore, GRADE_CODES, ZONES, empty_zones, file_sha256, scan_cases, utc_now
from PneumoconiosisLib.model_config import WEIGHTS_SHA256
from PneumoconiosisLib.partition import zone_map
from PneumoconiosisLib.runtime import worker_command


def check(condition, message, checks):
    if not condition:
        raise AssertionError(message)
    checks.append(message)
    print("PASS: " + message, flush=True)


def create_dicom(source):
    """Wrap the fictional PNG in an uncompressed DX DICOM; retain stored row order."""
    with Image.open(ROOT / "docs/img/synthetic-chest.png") as image:
        pixels = np.asarray(image.convert("L"), dtype=np.uint16) * 16
    source.mkdir(parents=True)
    path = source / "合成胸片.dcm"
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = DigitalXRayImageStorageForPresentation
    meta.MediaStorageSOPInstanceUID = generate_uid()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds = FileDataset(str(path), {}, file_meta=meta, preamble=b"\0" * 128)
    ds.SOPClassUID, ds.SOPInstanceUID = meta.MediaStorageSOPClassUID, meta.MediaStorageSOPInstanceUID
    ds.StudyInstanceUID, ds.SeriesInstanceUID = generate_uid(), generate_uid()
    ds.Modality, ds.PatientName, ds.PatientID = "DX", "SYNTHETIC^DEMO", "SYNTHETIC-DEMO"
    ds.PatientOrientation = ["L", "F"]
    ds.Rows, ds.Columns = pixels.shape
    ds.SamplesPerPixel, ds.PhotometricInterpretation = 1, "MONOCHROME2"
    ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 16, 12, 11, 0
    ds.WindowCenter, ds.WindowWidth = 2048, 4096
    ds.PixelData = pixels.astype("<u2").tobytes()
    ds.save_as(path, enforce_file_format=True)
    return path, pixels


def restore_and_export(directory):
    """This phase runs in a new interpreter, with no live SQLite handles inherited."""
    checks = []
    expected = json.loads((directory / "expected.json").read_text(encoding="utf-8"))
    with_store = AnnotationStore(directory / "project 标注")
    try:
        record = with_store.get(expected["case_id"], expected["reader_id"])
        check(record == expected, "all saved fields survive a new Python process", checks)
        check(with_store.get(expected["case_id"], "reader-02")["zones"] == empty_zones(),
              "reader records remain independent", checks)
        export_directory = with_store.export(directory / "exports", [expected["case_id"]])
    finally:
        with_store.close()

    reader = expected["reader_id"]
    bundle = export_directory / reader
    paths = [bundle / (reader + suffix) for suffix in (".csv", ".json", "-spatial.csv", "-images.csv")]
    text = paths[1].read_text(encoding="utf-8")
    package = json.loads(text)
    check(package["schema_version"] == 6 and len(package["records"]) == 1 and len(package["history"]) == 2,
          "JSON contains the current record and both saved revisions", checks)
    exported = copy.deepcopy(package["records"][0])
    reference = exported["partition"].pop("zone_map")
    check(exported == expected, "export preserves exact quality, scores, geometry and model provenance", checks)
    with paths[0].open(encoding="utf-8-sig", newline="") as stream:
        scores = list(csv.DictReader(stream))
    check(len(scores) == 6 and all(row["status"] == "complete" and row["quality_grade"] == "2" for row in scores),
          "score CSV contains six completed lung zones and whole-image quality", checks)
    check(all(row["profusion_code"] == GRADE_CODES[expected["zones"][row["zone"]]["grade"]] for row in scores),
          "CSV preserves all fixture grades using safe profusion codes", checks)
    with paths[2].open(encoding="utf-8-sig", newline="") as stream:
        vertices = list(csv.DictReader(stream))
    expected_vertices = [(item["id"], index, *point) for item in expected["spatial_annotations"]
                         for index, point in enumerate(item["points_ijk"])]
    exported_vertices = [(row["annotation_id"], int(row["vertex_index"]), float(row["i"]), float(row["j"]), float(row["k"]))
                         for row in vertices]
    check(exported_vertices == expected_vertices,
          "spatial CSV preserves exact point coordinates and ordered polygon vertices", checks)
    with paths[3].open(encoding="utf-8-sig", newline="") as stream:
        images = list(csv.DictReader(stream))
    check(images == [{"case_id": expected["case_id"], "source_relative_path": "./合成胸片.dcm",
                      "source_sha256": file_sha256(directory / "input 胸片/合成胸片.dcm")}],
          "image index links the annotation to its original DICOM and file hash", checks)

    references = [reference]
    for annotation in package["records"] + [item["annotation"] for item in package["history"]]:
        references.extend(annotation["audio"])
        references.extend((annotation["partition"]["baseline"], annotation["partition"]["masks"]))
    for relative, digest in dict.fromkeys((asset["path"], asset["sha256"]) for asset in references):
        check(file_sha256(resolve_asset(bundle, relative)) == digest,
              "exported attachment hash: " + relative, checks)
    store = AnnotationStore(directory / "project 标注")
    try:
        labels = zone_map(*store.assets.lungs(expected["partition"]["masks"]), expected["partition"]["landmarks"])
    finally:
        store.close()
    with np.load(bundle / reference["path"], allow_pickle=False) as arrays:
        check(np.array_equal(arrays["zones"], labels), "exported six-zone map matches the saved masks and landmarks", checks)
    dicom = pydicom.dcmread(directory / "input 胸片/合成胸片.dcm", stop_before_pixels=True)
    check(str(dicom.SOPInstanceUID) not in text and str(directory.resolve()) not in text,
          "JSON excludes raw DICOM UID and source path", checks)
    (directory / "restore-report.json").write_text(json.dumps({"checks": checks, "bundle": str(bundle.relative_to(directory))}, indent=2), encoding="utf-8")


def run(output, report):
    checks = report["checks"]
    directory = output / uuid.uuid4().hex
    directory.mkdir(parents=True)
    report["run_directory"] = directory.name
    path, pixels = create_dicom(directory / "input 胸片")
    cases, skipped = scan_cases(path.parent)
    check(len(cases) == 1 and skipped == 0, "synthetic DX is scanned from a Unicode path", checks)
    decoded = pydicom.dcmread(path).pixel_array
    check(np.array_equal(decoded, pixels), "DICOM preserves every synthetic input pixel", checks)
    check(sounddevice.get_portaudio_version()[0] > 0, "audio dependency and native PortAudio library load without a microphone", checks)
    np.save(directory / "model-input.npy", decoded.astype(np.float32), allow_pickle=False)
    project = directory / "project 标注"
    project.mkdir()
    job = {"id": uuid.uuid4().hex, "case_id": cases[0]["case_id"], "reader_id": "reader-01",
           "source_sha256": cases[0]["source_sha256"], "project": str(project), "input": str(directory / "model-input.npy")}
    started = time.monotonic()
    worker = subprocess.run(worker_command(ROOT, "model"),
                            input=json.dumps(job, ensure_ascii=True) + "\n", capture_output=True,
                            text=True, encoding="utf-8", timeout=180, cwd=ROOT)
    (directory / "model.stdout.jsonl").write_text(worker.stdout, encoding="utf-8")
    (directory / "model.stderr.log").write_text(worker.stderr, encoding="utf-8")
    worker.check_returncode()
    events = [json.loads(line) for line in worker.stdout.splitlines() if line.strip()]
    results = [event for event in events if event["event"] == "result" and event["job"] == job["id"]]
    check(len(results) == 1 and not any(event["event"] == "error" for event in events),
          "real background worker completes CPU model inference", checks)
    partition = results[0]["partition"]
    report["model"] = partition["model"]
    report["model_process_seconds"] = round(time.monotonic() - started, 3)
    check(partition["model"]["weights_sha256"] == WEIGHTS_SHA256 and partition["source_sha256"] == cases[0]["source_sha256"],
          "inference binds verified weights to the exact synthetic DICOM", checks)
    store = AnnotationStore(project)
    try:
        store.import_cases(cases)
        draft = store.save(cases[0]["case_id"], "reader-01", empty_zones(), partition=partition)
        check(draft["zones"] == empty_zones() and draft["quality"]["grade"] is None and draft["partition"]["state"] == "pending_review",
              "automatic segmentation leaves quality and profusion unread and awaits review", checks)
        right, left = store.assets.lungs(partition["masks"])
        labels = zone_map(right, left, partition["landmarks"])
        check(set(np.unique(labels)) == set(range(7)) and np.array_equal(labels > 0, right | left),
              "six nonempty zones cover exactly the inferred lung masks", checks)
        # Explicit test scores and confirmation, never model-generated clinical ratings.
        grades = ("0/-", "0/0", "0/1", "2/3", "3/3", "3/+")
        zones = {zone: {"state": "scored", "grade": grade} for zone, grade in zip(ZONES, grades)}
        try:
            store.save(cases[0]["case_id"], "reader-01", zones, "complete", quality={"grade": 2}, partition=partition)
        except ValueError:
            check(store.get(cases[0]["case_id"], "reader-01") == draft, "unconfirmed partition cannot replace the saved draft as complete", checks)
        else:
            raise AssertionError("Unconfirmed partition was incorrectly accepted as complete")
        partition.update(state="confirmed", confirmed_at=utc_now(), confirmed_by="reader-01")
        rows, columns = decoded.shape
        spatial = [{"id": uuid.uuid4().hex, "kind": "circle", "label": "演示圆形", "points_ijk": [[columns * .3, rows * .4, 0], [columns * .36, rows * .44, 0]]},
                   {"id": uuid.uuid4().hex, "kind": "polygon", "label": "演示区域",
                    "points_ijk": [[columns * i, rows * j, 0] for i, j in ((.60, .52), (.75, .53), (.74, .66), (.59, .65))]}]
        audio = WaveRecording(project, cases[0]["case_id"], "reader-01", 16000)
        audio.write((np.sin(np.arange(3200) * 2 * np.pi * 440 / 16000) * 2000).astype("<i2").tobytes())
        complete = store.save(cases[0]["case_id"], "reader-01", zones, "complete", quality={"grade": 2},
                              partition=partition, spatial_annotations=spatial, audio=[audio.finish()])
        check(complete["status"] == "complete", "explicit fixture scores, confirmation, points and synthetic PCM persist", checks)
    finally:
        store.close()
    (directory / "expected.json").write_text(json.dumps(complete, ensure_ascii=False, indent=2), encoding="utf-8")
    restored = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--verify", str(directory)],
                              capture_output=True, text=True, encoding="utf-8", timeout=60, cwd=ROOT)
    (directory / "restore.stdout.log").write_text(restored.stdout, encoding="utf-8")
    (directory / "restore.stderr.log").write_text(restored.stderr, encoding="utf-8")
    restored.check_returncode()
    verification = json.loads((directory / "restore-report.json").read_text(encoding="utf-8"))
    checks.extend(verification["checks"])
    report["export"] = str(Path(directory.name) / verification["bundle"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "local-data/qa/synthetic-pipeline")
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.verify:
        restore_and_export(args.verify.resolve())
        return
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = {"status": "failed", "checks": [], "started_at": utc_now(), "commit": os.environ.get("GITHUB_SHA"),
              "platform": platform.platform(), "machine": platform.machine(), "python": sys.version,
              "pointer_bits": struct.calcsize("P") * 8, "fixture_sha256": file_sha256(ROOT / "docs/img/synthetic-chest.png"),
              "dependencies": {name: importlib.metadata.version(name) for name in ("torch", "torchvision", "torchxrayvision", "numpy", "scipy", "Pillow", "pydicom", "sounddevice")}}
    try:
        run(output, report)
        report["status"] = "passed"
    except Exception:
        report["error"] = traceback.format_exc()
        raise
    finally:
        report["finished_at"] = utc_now()
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        summary = f"## Synthetic backend pipeline: {report['status']}\n\n{report['platform']} / {report['machine']}\n\n"
        summary += "\n".join("- " + check for check in report["checks"])
        summary += "\n\nSynthetic fixture only. No Slicer GUI, physical microphone or clinical accuracy validation.\n"
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
                stream.write(summary)
    print(f"Synthetic pipeline passed: {len(report['checks'])} checks", flush=True)


if __name__ == "__main__":
    main()
