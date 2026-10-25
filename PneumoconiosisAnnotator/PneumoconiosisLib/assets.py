"""Immutable local assets; SQLite revisions retain references to earlier versions."""
from __future__ import annotations

import hashlib
import io
import os
import tempfile
from pathlib import Path


def atomic_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def resolve_asset(directory, relative):
    directory = Path(directory).resolve()
    relative = str(relative)
    if "\\" in relative or ":" in relative or ".." in Path(relative).parts or Path(relative).is_absolute():
        raise ValueError("附件路径必须是项目内的相对路径。")
    result = (directory / relative).resolve()
    if not result.is_relative_to(directory) or result == directory:
        raise ValueError("附件路径超出项目目录。")
    return result


class AssetStore:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()

    def put(self, data, suffix, category="masks"):
        digest = hashlib.sha256(data).hexdigest()
        relative = f"assets/{category}/{digest}.{suffix}"
        path = resolve_asset(self.directory, relative)
        if not path.exists():
            atomic_bytes(path, data)
        elif hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("已有附件内容与校验值不符，请恢复备份。")
        return {"path": relative, "sha256": digest}

    def read(self, reference):
        path = resolve_asset(self.directory, reference["path"])
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != reference["sha256"]:
            raise ValueError("附件校验失败，请核对备份。")
        return data

    def put_lungs(self, right, left):
        import numpy as np
        stream = io.BytesIO()
        np.savez_compressed(stream, right=np.asarray(right, dtype=np.uint8), left=np.asarray(left, dtype=np.uint8))
        return self.put(stream.getvalue(), "npz")

    def lungs(self, reference):
        import numpy as np
        with np.load(io.BytesIO(self.read(reference)), allow_pickle=False) as arrays:
            return arrays["right"].astype(bool), arrays["left"].astype(bool)
