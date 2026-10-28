"""Explicit project creation/opening; image identity survives folder relocation."""
import json
from pathlib import Path
import sqlite3


def validate_project(directory, cases, *, create):
    directory = Path(directory).expanduser().resolve()
    if create:
        if directory.exists() and (not directory.is_dir() or any(
                entry.name != ".DS_Store" for entry in directory.iterdir())):
            raise ValueError("新建项目请选择一个空文件夹；已有标注请使用「打开项目」。")
        return directory
    database = directory / "annotations.sqlite3"
    if not database.is_file():
        raise ValueError("这里没有标注项目。请选择含 annotations.sqlite3 的项目文件夹；导出交付包不能用于继续标注。")
    # Opening an arbitrary directory must never create or modify a database.
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    try:
        if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise ValueError("标注项目版本不受支持。")
        for table, columns in {
            "cases": "case_id, metadata_json", "annotations": "case_id, reader_id, payload_json, updated_at",
            "revisions": "id, case_id, reader_id, created_at, payload_json", "preferences": "key, value",
        }.items():
            connection.execute(f"SELECT {columns} FROM {table} LIMIT 0")
        saved = {row[0]: json.loads(row[1])["source_sha256"]
                 for row in connection.execute("SELECT case_id, metadata_json FROM cases")}
        selected = {case["case_id"]: case["source_sha256"] for case in cases}
        if saved != selected:
            raise ValueError("胸片文件夹与标注项目不匹配：病例列表或原片内容不同。请选择该项目对应的整批原片。")
    except (sqlite3.Error, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("标注项目不完整或已损坏，请选择完整项目文件夹。") from exc
    finally:
        connection.close()
    return directory
