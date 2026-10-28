"""Research annotations. Source images remain read-only; ratings are never inferred."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import sqlite3
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from .assets import AssetStore, atomic_bytes
from .geometry import circle_issue, measure, measurement_scale

GRADES = ("0/-", "0/0", "0/1", "1/0", "1/1", "1/2", "2/1", "2/2", "2/3", "3/2", "3/3", "3/+")
ZONES = ("RU", "RM", "RL", "LU", "LM", "LL")
ZONE_NAMES = dict(zip(ZONES, ("右上", "右中", "右下", "左上", "左中", "左下")))
GRADE_CODES = {grade: "P" + grade.replace("/", "_").replace("-", "minus").replace("+", "plus") for grade in GRADES}
STANDARD = "GBZ 70-2015 + amendment 1"
QUALITY_GRADES = {1: "优片", 2: "良片", 3: "差片", 4: "废片"}
COORDINATES = {"system": "image_ijk", "units": "pixel", "index_base": 0,
               "axes": ["column", "row", "slice"], "origin": "center of top-left stored pixel",
               "polygon": "ordered vertices; last vertex connects to first",
               "circle": "two diameter endpoints; circle in the measurement plane defined by image.measurement_scale"}


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def empty_zones():
    return {zone: {"state": "unread", "grade": None} for zone in ZONES}


def has_reading_activity(record):
    """Ignore navigation/display saves and unreviewed machine-only partitions."""
    quality = record.get("quality") or {}
    return (any(zone["state"] in ("scored", "not_scorable") for zone in record["zones"].values())
            or quality.get("grade") is not None or bool(quality.get("reason", "").strip())
            or bool(record.get("spatial_annotations")) or bool(record.get("audio"))
            or (record.get("partition") or {}).get("state") in ("edited", "confirmed"))


def validate_reader(reader):
    reader = str(reader).strip()
    if not reader or len(reader) > 64 or not all(c.isalnum() or c in "_-" for c in reader):
        raise ValueError("读者编号请使用 1–64 位文字、数字、短横线或下划线。")
    return reader


def validate_zones(zones):
    if set(zones) != set(ZONES):
        raise ValueError("必须包含固定的六肺区。")
    normalized = {}
    for zone in ZONES:
        value = zones[zone]
        state, grade = value.get("state"), value.get("grade")
        if state not in ("unread", "scored", "not_scorable"):
            raise ValueError("无效的区域评价状态。")
        if (state == "scored" and grade not in GRADES) or (state != "scored" and grade is not None):
            raise ValueError("密集度必须为标准 12 小级；未评和不可评价不能填写密集度。")
        normalized[zone] = {"state": state, "grade": grade}
    return normalized


def validate_complete(zones):
    zones = validate_zones(zones)
    unread = [ZONE_NAMES[z] for z in ZONES if zones[z]["state"] == "unread"]
    if unread:
        raise ValueError("尚未评价：" + "、".join(unread))


def validate_quality(quality, complete=False):
    value = quality or {"grade": None, "reason": ""}
    grade, reason = value.get("grade"), str(value.get("reason", "")).strip()
    if grade is not None and (type(grade) is not int or grade not in QUALITY_GRADES):
        raise ValueError("整片质量请选择 1–4 级，或保留未评。")
    if len(reason) > 2000:
        raise ValueError("质量缺陷说明最多 2000 字。")
    if complete and grade is None:
        raise ValueError("整片质量尚未评价。")
    return {"grade": grade, "reason": reason}


def polygon_issue(points):
    """A usable 2D region is a non-degenerate, simple closed polygon."""
    if len(points) < 3:
        return "区域至少需要 3 个顶点。"
    xy = [(p[0], p[1]) for p in points]
    if len(set(xy)) != len(xy):
        return "区域有重合顶点，请拖开或删除重复点。"

    def cross(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    def on_segment(a, b, p):
        return (abs(cross(a, b, p)) < 1e-8 and
                min(a[0], b[0]) - 1e-8 <= p[0] <= max(a[0], b[0]) + 1e-8 and
                min(a[1], b[1]) - 1e-8 <= p[1] <= max(a[1], b[1]) + 1e-8)

    edges = list(zip(xy, xy[1:] + xy[:1]))
    for i, (a, b) in enumerate(edges):
        for j in range(i + 1, len(edges)):
            if j == i + 1 or (i == 0 and j == len(edges) - 1):
                continue
            c, d = edges[j]
            crossed = cross(a, b, c) * cross(a, b, d) < 0 and cross(c, d, a) * cross(c, d, b) < 0
            if crossed or any((on_segment(a, b, c), on_segment(a, b, d), on_segment(c, d, a), on_segment(c, d, b))):
                return "区域轮廓存在交叉，请拖动顶点修正。"
    if abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in edges)) < 1e-6:
        return "区域面积为零，请移动顶点。"
    # Collinear adjacent edges must not fold back onto each other.
    for i, b in enumerate(xy):
        a, c = xy[i - 1], xy[(i + 1) % len(xy)]
        if abs(cross(a, b, c)) < 1e-8 and (on_segment(a, b, c) or on_segment(b, c, a)):
            return "区域边界重叠，请移动顶点。"
    return None


def shape_issue(kind, points):
    return circle_issue(points) if kind == "circle" else polygon_issue(points)


def validate_spatial(annotations, image, complete=False):
    if not isinstance(annotations, list):
        raise ValueError("空间标注必须为列表。")
    result, ids = [], set()
    for annotation in annotations:
        identifier = str(annotation["id"])
        uuid.UUID(identifier)
        if identifier in ids:
            raise ValueError("空间标注编号重复。")
        ids.add(identifier)
        kind, label = annotation["kind"], str(annotation["label"]).strip()
        if kind not in ("circle", "polygon") or not label or len(label) > 80:
            raise ValueError("标注类型无效，或名称为空 / 超过 80 字。")
        positions = annotation["points_ijk"]
        if not positions or (kind == "circle" and len(positions) > 2):
            raise ValueError("圆形最多有两个直径端点；区域草稿至少需要一个顶点。")
        points = []
        for position in positions:
            if len(position) != 3 or not all(math.isfinite(float(v)) for v in position):
                raise ValueError("标注坐标必须为有限的三维像素坐标。")
            x, y, z = map(float, position)
            if abs(z) > 1e-3 or not (-0.5 <= x <= image["columns"] - 0.5 and -0.5 <= y <= image["rows"] - 0.5):
                raise ValueError("标注点超出胸片边界或离开图像平面，请拖回图内或删除该标注。")
            points.append([round(x, 6), round(y, 6), 0.0])
        issue = shape_issue(kind, points)
        if complete and issue:
            raise ValueError(label + "：" + issue)
        result.append({"id": identifier, "kind": kind, "label": label, "points_ijk": points,
                       "state": "draft" if issue else "ready",
                       "measurement": None if issue else measure(kind, points, image)})
    return result


def completion_issues(zones, quality, spatial, partition, image):
    """Cheap form checks shared by live feedback and completion, without loading assets."""
    from .partition import validate_partition_confirmation

    issues = {}
    for area, validate in (
        ("ratings", lambda: validate_complete(zones)),
        ("quality", lambda: validate_quality(quality, complete=True)),
        ("spatial", lambda: validate_spatial(spatial, image, complete=True)),
        ("partition", lambda: validate_partition_confirmation(partition)),
    ):
        try:
            validate()
        except ValueError as exc:
            issues[area] = str(exc)
    return issues


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def scan_cases(source):
    """Index single-frame CR/DX images. Do not record patient names or identifiers."""
    import pydicom

    source = Path(source).expanduser().resolve()
    if not source.is_dir():
        raise ValueError("请选择存在的 DICOM 目录。")
    if any(part == "__MACOSX" or part.startswith("._") for part in source.parts):
        raise ValueError("这是 Mac 压缩包的辅助目录，请选择存放胸片的目录或解压后的上层目录。")
    paths = []
    for directory, subdirectories, filenames in os.walk(source):
        # Finder ZIP archives contain AppleDouble files and a parallel resource tree.
        subdirectories[:] = sorted(name for name in subdirectories
                                   if name != "__MACOSX" and not name.startswith("._"))
        paths.extend(Path(directory) / name for name in filenames
                     if not name.startswith("._") and Path(name).suffix.lower() == ".dcm")
    paths.sort()

    def read(path):
        try:
            ds = pydicom.dcmread(path, stop_before_pixels=True)
        except (OSError, pydicom.errors.InvalidDicomError):
            return None
        if str(ds.get("Modality", "")) not in ("DX", "CR") or int(ds.get("NumberOfFrames", 1)) != 1:
            return None
        if int(ds.get("SamplesPerPixel", 1)) != 1 or str(ds.get("PhotometricInterpretation", "")) not in ("MONOCHROME1", "MONOCHROME2"):
            return None
        if not all(ds.get(key) for key in ("SOPInstanceUID", "StudyInstanceUID", "SeriesInstanceUID", "Rows", "Columns")):
            return None
        sop = str(ds.SOPInstanceUID)
        return {
            "case_id": "CXR-" + hashlib.sha256(sop.encode()).hexdigest()[:16],
            "file_path": str(path), "source_sha256": file_sha256(path),
            "source_relative_path": path.relative_to(source).as_posix(),
            "sop_uid": sop, "study_uid": str(ds.StudyInstanceUID), "series_uid": str(ds.SeriesInstanceUID),
            "rows": int(ds.Rows), "columns": int(ds.Columns), "modality": str(ds.Modality),
            "measurement_scale": measurement_scale(ds),
            "orientation_known": bool(ds.get("ImageOrientationPatient")) and bool(ds.get("ViewPosition")),
        }

    with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 1)) as pool:
        indexed = list(pool.map(read, paths))
    unique = {}
    for case in indexed:
        if case is None:
            continue
        previous = unique.get(case["case_id"])
        if previous and previous["source_sha256"] != case["source_sha256"]:
            raise ValueError("发现相同图像标识但内容不同的文件，已停止导入，请先核对来源。")
        unique[case["case_id"]] = case
    if not unique:
        raise ValueError("没有找到可用的单帧 CR/DX 灰度 DICOM（.dcm）图像。")
    return sorted(unique.values(), key=lambda c: c["case_id"]), len(paths) - len(unique)


class AnnotationStore:
    def __init__(self, directory):
        self.directory = Path(directory).expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "annotations.sqlite3"
        self.assets = AssetStore(self.directory)
        self.connection = sqlite3.connect(self.path, timeout=5)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            self.connection.close()
            raise ValueError("标注库版本不受此模块支持。")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS cases (
                case_id TEXT PRIMARY KEY,
                metadata_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS annotations (
                case_id TEXT NOT NULL REFERENCES cases(case_id),
                reader_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(case_id, reader_id)
            );
            CREATE TABLE IF NOT EXISTS revisions (
                id INTEGER PRIMARY KEY,
                case_id TEXT NOT NULL REFERENCES cases(case_id),
                reader_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS preferences (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            PRAGMA user_version=1;
        """)

    def close(self):
        self.connection.close()

    def import_cases(self, cases):
        with self.connection:
            for case in cases:
                old = self.connection.execute("SELECT metadata_json FROM cases WHERE case_id=?", (case["case_id"],)).fetchone()
                if old and json.loads(old[0])["source_sha256"] != case["source_sha256"]:
                    raise ValueError("原始图像内容已变化，不能沿用旧标注。请核对数据或使用新项目。")
                self.connection.execute("INSERT OR REPLACE INTO cases VALUES (?, ?)", (case["case_id"], json.dumps(case, ensure_ascii=False)))

    def get(self, case_id, reader):
        reader = validate_reader(reader)
        row = self.connection.execute("SELECT payload_json, updated_at FROM annotations WHERE case_id=? AND reader_id=?", (case_id, reader)).fetchone()
        if row:
            value = json.loads(row[0])
            if any(s["kind"] not in ("circle", "polygon") for s in value.get("spatial_annotations", [])):
                raise ValueError("此病例含不支持的旧版标注，无法当作圆形读取。请使用独立的新标注项目；旧数据仍保留。")
            value.setdefault("spatial_annotations", [])
            value.setdefault("quality", {"grade": None, "reason": ""})
            value.setdefault("partition", None)
            value.setdefault("audio", [])
            if value["status"] == "complete" and value["quality"]["grade"] is None:
                value["status"] = "draft"
            value["updated_at"] = row[1]
            return value
        return {"schema_version": 6, "standard": STANDARD, "case_id": case_id, "reader_id": reader,
                "zones": empty_zones(), "spatial_annotations": [], "quality": {"grade": None, "reason": ""},
                "partition": None, "audio": [], "status": "draft", "display": {}, "updated_at": None}

    def save(self, case_id, reader, zones, status="draft", display=None, spatial_annotations=None,
             quality=None, partition=None, audio=None):
        reader = validate_reader(reader)
        zones = validate_zones(zones)
        if status not in ("draft", "complete"):
            raise ValueError("无效的病例状态。")
        if status == "complete":
            validate_complete(zones)
        quality = validate_quality(quality, complete=status == "complete")
        case_row = self.connection.execute("SELECT metadata_json FROM cases WHERE case_id=?", (case_id,)).fetchone()
        if not case_row:
            raise ValueError("病例不存在。")
        metadata = json.loads(case_row[0])
        image = {k: metadata[k] for k in ("columns", "rows")}
        image["measurement_scale"] = metadata.get("measurement_scale", measurement_scale({}))
        spatial = validate_spatial(spatial_annotations or [], image, complete=status == "complete")
        from .audio_files import validate_audio
        audio = validate_audio(audio or [], self.directory)
        if any(not a["path"].startswith(f"assets/audio/{case_id}/{reader}/") for a in audio):
            raise ValueError("录音不属于当前病例和读者，已停止保存。")
        if partition is not None:
            from .partition import validate_partition
            partition = validate_partition(partition, image, self.assets, complete=status == "complete")
            if partition["source_sha256"] != metadata["source_sha256"]:
                raise ValueError("分区不对应当前原始胸片，已停止保存。")
            if partition["state"] == "confirmed" and partition.get("confirmed_by") != reader:
                raise ValueError("分区确认不属于当前读者，请由当前读者重新确认。")
        payload = {"schema_version": 6, "standard": STANDARD, "case_id": case_id, "reader_id": reader,
                   "zones": zones, "status": status, "display": display or {},
                   "image": image, "spatial_annotations": spatial, "quality": quality,
                   "partition": partition, "audio": audio}
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
        timestamp = utc_now()
        with self.connection:
            row = self.connection.execute("SELECT payload_json FROM annotations WHERE case_id=? AND reader_id=?", (case_id, reader)).fetchone()
            if not row or row[0] != encoded:
                self.connection.execute("INSERT OR REPLACE INTO annotations VALUES (?, ?, ?, ?)", (case_id, reader, encoded, timestamp))
                self.connection.execute("INSERT INTO revisions(case_id, reader_id, created_at, payload_json) VALUES (?, ?, ?, ?)", (case_id, reader, timestamp, encoded))
        return self.get(case_id, reader)

    def preference(self, key, default=None):
        row = self.connection.execute("SELECT value FROM preferences WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_preference(self, key, value):
        with self.connection:
            self.connection.execute("INSERT OR REPLACE INTO preferences VALUES (?, ?)", (key, json.dumps(value)))

    def completion(self, case_ids, reader):
        rows = self.connection.execute("SELECT case_id, payload_json FROM annotations WHERE reader_id=?", (reader,)).fetchall()
        active = set(case_ids)
        result = {}
        for case_id, payload in rows:
            if case_id in active:
                value = json.loads(payload)
                result[case_id] = value["status"] if value.get("quality", {}).get("grade") is not None else "draft"
        return result

    def recover_audio(self):
        from .audio_files import pending_recordings
        recovered = 0
        for case_id, reader, recording in pending_recordings(self.directory):
            if not self.connection.execute("SELECT 1 FROM cases WHERE case_id=?", (case_id,)).fetchone():
                continue
            record = self.get(case_id, reader)
            if any(a["id"] == recording["id"] for a in record["audio"]):
                continue
            self.save(case_id, reader, record["zones"], "draft", record["display"],
                      record["spatial_annotations"], record["quality"], record["partition"], record["audio"] + [recording])
            recovered += 1
        return recovered

    def export_snapshot(self, case_ids):
        """Capture participating readers and source metadata in one read transaction."""
        case_ids = tuple(dict.fromkeys(case_ids))
        wanted = set(case_ids)
        with self.connection:
            self.connection.execute("BEGIN")
            readers = {r[0] for r in self.connection.execute("SELECT reader_id, case_id, payload_json FROM annotations")
                       if r[1] in wanted and has_reading_activity(json.loads(r[2]))}
            histories = {}
            for revision, case_id, reader, at, payload in self.connection.execute(
                    "SELECT id, case_id, reader_id, created_at, payload_json FROM revisions ORDER BY id"):
                if case_id not in wanted:
                    continue
                annotation = json.loads(payload)
                histories.setdefault(reader, []).append({"revision": revision, "at": at, "annotation": annotation})
                if has_reading_activity(annotation):
                    readers.add(reader)
            if not readers:
                raise ValueError("当前病例列表还没有实际阅片记录，暂不能导出。")
            images = []
            dimensions = {}
            sources = {}
            for case_id in case_ids:
                metadata = json.loads(self.connection.execute("SELECT metadata_json FROM cases WHERE case_id=?", (case_id,)).fetchone()[0])
                sources[case_id] = metadata
                dimensions[case_id] = {k: metadata[k] for k in ("columns", "rows")}
                dimensions[case_id]["measurement_scale"] = metadata.get("measurement_scale", measurement_scale({}))
                if not metadata.get("source_relative_path"):
                    raise ValueError("缺少原片相对路径，请重新打开胸片目录后再导出。")
                # ./ preserves the exact filename while preventing spreadsheet formulas.
                images.append({"case_id": case_id,
                               "source_relative_path": "./" + metadata["source_relative_path"],
                               "source_sha256": metadata["source_sha256"]})
            packages = []
            exported_at = utc_now()
            for reader in sorted(readers):
                reader = validate_reader(reader)
                records = [self.get(case_id, reader) for case_id in case_ids]
                for record in records:
                    record["image"] = dimensions[record["case_id"]]
                    record["reading_state"] = record["status"] if has_reading_activity(record) else "unread"
                packages.append({"schema_version": 6, "standard": STANDARD, "exported_at": exported_at, "reader_id": reader,
                                 "coordinate_system": COORDINATES, "grade_codes": GRADE_CODES, "zone_names": ZONE_NAMES,
                                 "quality_grades": QUALITY_GRADES, "records": records, "history": histories.get(reader, [])})
        return {"packages": packages, "images": images, "sources": sources}

    def export(self, destination, case_ids):
        """Export all participating readers from one snapshot into a dated directory."""
        from .delivery import write_delivery_index
        destination = Path(destination).expanduser().resolve()
        destination.mkdir(parents=True, exist_ok=True)
        snapshot = self.export_snapshot(case_ids)
        stem = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        # Rename a complete bundle into place; a failure leaves only a clearly
        # marked hidden partial directory, never a successful-looking export.
        temporary = Path(tempfile.mkdtemp(prefix=".partial-export-", dir=destination))
        final_directory = destination / stem
        for package in snapshot["packages"]:
            self._write_reader_export(temporary / package["reader_id"], package, snapshot["images"])
        write_delivery_index(temporary, snapshot)
        os.replace(temporary, final_directory)
        return final_directory

    def _write_reader_export(self, directory, package, images):
        directory.mkdir()
        reader = package["reader_id"]
        records, history = package["records"], package["history"]
        copied = set()
        for record in records + [revision["annotation"] for revision in history]:
            references = list(record.get("audio", []))
            partition = record.get("partition")
            if partition:
                references.extend((partition["baseline"], partition["masks"]))
            for reference in references:
                if reference["path"] not in copied:
                    atomic_bytes(directory / reference["path"], self.assets.read(reference))
                    copied.add(reference["path"])
        for record in records:
            if record["partition"]:
                import numpy as np
                from .partition import ZONE_LABELS, zone_map
                partition = record["partition"]
                labels = zone_map(*self.assets.lungs(partition["masks"]), partition["landmarks"])
                stream = io.BytesIO()
                np.savez_compressed(stream, zones=labels)
                data = stream.getvalue()
                relative = "zone-maps/" + record["case_id"] + ".npz"
                atomic_bytes(directory / relative, data)
                partition["zone_map"] = {"path": relative, "sha256": hashlib.sha256(data).hexdigest(),
                                         "axes": ["row", "column"], "background": 0, "labels": ZONE_LABELS}
        json_path = directory / (reader + ".json")
        csv_path = directory / (reader + ".csv")
        spatial_path = directory / (reader + "-spatial.csv")
        images_path = directory / (reader + "-images.csv")
        buffer = io.StringIO(newline="")
        fields = ["case_id", "reader_id", "status", "standard", "quality_grade", "quality_name", "quality_reason",
                  "zone", "zone_name", "state", "profusion_code", "major_category", "alternative_category",
                  "partition_state", "audio_count", "audio_paths", "updated_at"]
        writer = csv.DictWriter(buffer, fieldnames=fields)
        writer.writeheader()
        for record in records:
            for zone in ZONES:
                value = record["zones"][zone]
                grade = value["grade"]
                parts = grade.split("/") if grade else ("", "")
                row = {"case_id": record["case_id"], "reader_id": reader, "status": record["status"], "standard": STANDARD,
                       "quality_grade": record["quality"]["grade"] if record["quality"]["grade"] is not None else "",
                       "quality_name": QUALITY_GRADES.get(record["quality"]["grade"], ""), "quality_reason": record["quality"]["reason"],
                       "zone": zone, "zone_name": ZONE_NAMES[zone], "state": value["state"],
                       "profusion_code": GRADE_CODES.get(grade, ""), "major_category": parts[0],
                       "alternative_category": parts[1], "updated_at": record["updated_at"] or "",
                       "partition_state": record["partition"]["state"] if record["partition"] else "not_generated",
                       "audio_count": len(record["audio"]), "audio_paths": "|".join(a["path"] for a in record["audio"])}
                writer.writerow({key: csv_safe(value) for key, value in row.items()})
        spatial_buffer = io.StringIO(newline="")
        spatial_fields = ["case_id", "reader_id", "annotation_id", "kind", "label", "state", "vertex_index", "i", "j", "k", "columns", "rows", "measurement_method", "measurement_unit", "diameter", "width", "spacing_source"]
        spatial_writer = csv.DictWriter(spatial_buffer, fieldnames=spatial_fields)
        spatial_writer.writeheader()
        for record in records:
            for annotation in record["spatial_annotations"]:
                for index, point in enumerate(annotation["points_ijk"]):
                    size = annotation.get("measurement") or {}
                    row = {"case_id": record["case_id"], "reader_id": reader,
                           "annotation_id": annotation["id"], "kind": annotation["kind"], "label": annotation["label"],
                           "state": annotation["state"], "vertex_index": index,
                           "i": point[0], "j": point[1], "k": point[2],
                           "columns": record["image"]["columns"], "rows": record["image"]["rows"]}
                    row.update(measurement_method=size.get("method", ""), measurement_unit=size.get("unit", ""),
                               diameter=size.get("diameter", ""),
                               width=size.get("width", ""), spacing_source=size.get("source", ""))
                    spatial_writer.writerow({key: csv_safe(value) for key, value in row.items()})
        images_buffer = io.StringIO(newline="")
        images_writer = csv.DictWriter(images_buffer, fieldnames=["case_id", "source_relative_path", "source_sha256"])
        images_writer.writeheader()
        images_writer.writerows(images)
        atomic_write(json_path, json.dumps(package, ensure_ascii=False, indent=2, allow_nan=False))
        atomic_write(csv_path, "\ufeff" + buffer.getvalue())
        atomic_write(spatial_path, "\ufeff" + spatial_buffer.getvalue())
        atomic_write(images_path, "\ufeff" + images_buffer.getvalue())


def csv_safe(value):
    if not isinstance(value, str):
        return value
    # Protect spreadsheet consumers from formulas in reader-entered labels.
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")) else value


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
