"""Research annotations. Source images remain read-only; ratings are never inferred."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

GRADES = ("0/-", "0/0", "0/1", "1/0", "1/1", "1/2", "2/1", "2/2", "2/3", "3/2", "3/3", "3/+")
ZONES = ("RU", "RM", "RL", "LU", "LM", "LL")
ZONE_NAMES = dict(zip(ZONES, ("右上", "右中", "右下", "左上", "左中", "左下")))
GRADE_CODES = {grade: "P" + grade.replace("/", "_").replace("-", "minus").replace("+", "plus") for grade in GRADES}
STANDARD = "GBZ 70-2015 + amendment 1"


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def empty_zones():
    return {zone: {"state": "unread", "grade": None} for zone in ZONES}


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


def validate_complete(zones, note):
    zones = validate_zones(zones)
    unread = [ZONE_NAMES[z] for z in ZONES if zones[z]["state"] == "unread"]
    if unread:
        raise ValueError("尚未评价：" + "、".join(unread))
    if any(v["state"] == "not_scorable" for v in zones.values()) and not note.strip():
        raise ValueError("请在备注中说明不可评价的肺区及原因。")


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
    paths = sorted(p for p in source.rglob("*") if p.is_file() and p.suffix.lower() == ".dcm")

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
            "sop_uid": sop, "study_uid": str(ds.StudyInstanceUID), "series_uid": str(ds.SeriesInstanceUID),
            "rows": int(ds.Rows), "columns": int(ds.Columns), "modality": str(ds.Modality),
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
            value["updated_at"] = row[1]
            return value
        return {"schema_version": 1, "standard": STANDARD, "case_id": case_id, "reader_id": reader,
                "zones": empty_zones(), "note": "", "status": "draft", "display": {}, "updated_at": None}

    def save(self, case_id, reader, zones, note, status="draft", display=None):
        reader = validate_reader(reader)
        zones = validate_zones(zones)
        if status not in ("draft", "complete"):
            raise ValueError("无效的病例状态。")
        if status == "complete":
            validate_complete(zones, note)
        payload = {"schema_version": 1, "standard": STANDARD, "case_id": case_id, "reader_id": reader,
                   "zones": zones, "note": str(note), "status": status, "display": display or {}}
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
        return {row[0]: json.loads(row[1])["status"] for row in rows if row[0] in active}

    def export(self, destination, case_ids, reader):
        """Export one reader's consistent snapshot, including unread cases, without source identifiers."""
        reader = validate_reader(reader)
        destination = Path(destination).expanduser().resolve()
        destination.mkdir(parents=True, exist_ok=True)
        with self.connection:
            self.connection.execute("BEGIN")
            records = [self.get(case_id, reader) for case_id in case_ids]
            # The audit history and current ratings belong to the same read transaction.
            wanted = set(case_ids)
            history = [{"revision": r[0], "at": r[2], "annotation": json.loads(r[3])}
                       for r in self.connection.execute("SELECT id, case_id, created_at, payload_json FROM revisions WHERE reader_id=? ORDER BY id", (reader,)) if r[1] in wanted]
        package = {"schema_version": 1, "standard": STANDARD, "exported_at": utc_now(), "reader_id": reader,
                   "grade_codes": GRADE_CODES, "zone_names": ZONE_NAMES, "records": records, "history": history}
        stem = "pneumoconiosis-" + reader + "-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        json_path = destination / (stem + ".json")
        csv_path = destination / (stem + ".csv")
        buffer = io.StringIO(newline="")
        fields = ["case_id", "reader_id", "status", "standard", "zone", "zone_name", "state", "profusion_code", "major_category", "alternative_category", "note", "updated_at"]
        writer = csv.DictWriter(buffer, fieldnames=fields)
        writer.writeheader()
        for record in records:
            for zone in ZONES:
                value = record["zones"][zone]
                grade = value["grade"]
                parts = grade.split("/") if grade else ("", "")
                row = {"case_id": record["case_id"], "reader_id": reader, "status": record["status"], "standard": STANDARD,
                       "zone": zone, "zone_name": ZONE_NAMES[zone], "state": value["state"],
                       "profusion_code": GRADE_CODES.get(grade, ""), "major_category": parts[0],
                       "alternative_category": parts[1], "note": record["note"], "updated_at": record["updated_at"] or ""}
                writer.writerow({key: csv_safe(value) for key, value in row.items()})
        atomic_write(json_path, json.dumps(package, ensure_ascii=False, indent=2, allow_nan=False))
        atomic_write(csv_path, "\ufeff" + buffer.getvalue())
        return csv_path, json_path


def csv_safe(value):
    value = str(value)
    # Protect spreadsheet consumers from formulas in reader-entered notes.
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
