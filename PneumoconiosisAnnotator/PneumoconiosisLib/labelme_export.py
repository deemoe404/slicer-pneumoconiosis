"""Standalone Labelme image/shape export; no Labelme or Qt runtime dependency."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile

import numpy as np
from PIL import Image

from .assets import atomic_bytes
from .core import atomic_write, shape_issue, validate_reader, validate_spatial
from .geometry import circle_contour, circle_geometry
from .references import read_reference

LABELME_VERSION = "7.9.1"


def render_image(source, output):
    """Hash the bytes actually decoded, preserve stored raster order, and map to 8-bit."""
    data = Path(source["file_path"]).read_bytes()
    if hashlib.sha256(data).hexdigest() != source["source_sha256"]:
        raise ValueError(source["case_id"] + "：原始胸片内容已变化，已停止导出。")
    presentation = read_reference(io.BytesIO(data))
    pixels = presentation["pixels"]
    if pixels.shape != (source["rows"], source["columns"]):
        raise ValueError(source["case_id"] + "：原始胸片尺寸与标注记录不一致。")
    low = presentation["level"] - presentation["window"] / 2
    scaled = np.clip((pixels - low) / presentation["window"], 0, 1)
    if presentation["inverted"]:
        scaled = 1 - scaled
    buffer = io.BytesIO()
    Image.fromarray(np.rint(scaled * 255).astype(np.uint8)).save(buffer, format="PNG")
    png = buffer.getvalue()
    atomic_bytes(output, png)
    return {"png_sha256": hashlib.sha256(png).hexdigest(), "rows": source["rows"], "columns": source["columns"],
            "presentation": {"method": "dicom-modality-voi-or-minmax-v1", "bits": 8,
                             "mapped_min": low, "mapped_max": low + presentation["window"],
                             "inverted": presentation["inverted"], "spacing": list(presentation["spacing"])}}


def label_file(record):
    shapes, skipped = [], []
    for annotation in validate_spatial(record["spatial_annotations"], record["image"]):
        if annotation["state"] != "ready":
            skipped.append({"annotation_id": annotation["id"], "label": annotation["label"],
                            "reason": shape_issue(annotation["kind"], annotation["points_ijk"])})
            continue
        points, shape_type = annotation["points_ijk"], "polygon"
        if annotation["kind"] == "circle":
            center, radii = circle_geometry(points, record["image"])
            if np.isclose(radii[0], radii[1], rtol=1e-9, atol=0):
                # LabelMe circles store a center and one circumference point.
                points, shape_type = [center.tolist(), points[1][:2]], "circle"
            else:
                # Keep the original raster unchanged. Physical circles span unequal
                # pixel radii on anisotropic images, so use a contour in LabelMe.
                points = circle_contour(points, record["image"])
        shapes.append({"label": annotation["label"], "points": [p[:2] for p in points],
                       "group_id": None, "description": "", "shape_type": shape_type,
                       "flags": {}, "mask": None, "annotation_id": annotation["id"],
                       "source_kind": annotation["kind"], "source_points_ijk": annotation["points_ijk"],
                       "measurement": annotation["measurement"]})
    return {"version": LABELME_VERSION,
            "flags": {"case_complete": record["status"] == "complete", "has_skipped_shapes": bool(skipped)},
            "shapes": shapes, "imagePath": record["case_id"] + ".png", "imageData": None,
            "imageHeight": record["image"]["rows"], "imageWidth": record["image"]["columns"],
            "case_id": record["case_id"], "reader_id": record["reader_id"]}, skipped


def export_labelme(destination, snapshot, progress=None):
    """Generate every reader's image/JSON pairs and publish only a complete bundle."""
    packages, sources = snapshot["packages"], snapshot["sources"]
    if not packages or not sources:
        raise ValueError("当前病例列表还没有实际阅片记录，暂不能导出 LabelMe。")
    destination = Path(destination).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".partial-labelme-", dir=destination))
    final = destination / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    readers = [validate_reader(p["reader_id"]) for p in packages]
    for reader in readers:
        (temporary / reader).mkdir()
    total = len(sources) + sum(len(p["records"]) for p in packages)
    completed = 0
    report = {"schema_version": 1, "format": "labelme", "labelme_version": LABELME_VERSION,
              "exported_at": packages[0]["exported_at"], "readers": readers,
              "scope": "current circles and polygons; circles on non-square pixels use 128-vertex polygons; no lung masks or history",
              "empty_shapes_mean": "no exported spatial shapes; not a negative diagnosis",
              "images": [], "records": []}
    first = temporary / readers[0]
    rendered = {}
    with ThreadPoolExecutor(max_workers=min(4, os.cpu_count() or 1, len(sources))) as pool:
        futures = {pool.submit(render_image, source, first / (case_id + ".png")): case_id
                   for case_id, source in sources.items()}
        for future in as_completed(futures):
            case_id = futures[future]
            rendered[case_id] = future.result()
            completed += 1
            if progress:
                progress(completed, total)
    for image in snapshot["images"]:
        report["images"].append({**image, **rendered[image["case_id"]]})
    skipped_count = 0
    for package in packages:
        reader = package["reader_id"]
        folder = temporary / reader
        for record in package["records"]:
            case_id = record["case_id"]
            if folder != first:
                shutil.copyfile(first / (case_id + ".png"), folder / (case_id + ".png"))
            label, skipped = label_file(record)
            atomic_write(folder / (case_id + ".json"), json.dumps(label, ensure_ascii=False, indent=2, allow_nan=False))
            report["records"].append({"case_id": case_id, "reader_id": reader, "status": record["status"],
                                      "shape_count": len(label["shapes"]), "skipped_shapes": skipped})
            skipped_count += len(skipped)
            completed += 1
            if progress:
                progress(completed, total)
    atomic_write(temporary / "export-report.json", json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    os.replace(temporary, final)
    return {"directory": str(final), "reader_count": len(readers), "case_count": len(sources),
            "skipped_count": skipped_count}
