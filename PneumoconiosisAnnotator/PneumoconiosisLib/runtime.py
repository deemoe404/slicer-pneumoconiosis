"""Resolve immutable application files separately from writable annotation data."""
import os
from pathlib import Path
import sys


def packaged(root):
    return (Path(root) / "package.json").is_file()


def data_directory(root):
    override = os.environ.get("PNEUMO_DATA_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    if packaged(root):
        if sys.platform != "win32" or not os.environ.get("LOCALAPPDATA"):
            raise RuntimeError("Windows 用户数据目录不可用。")
        return Path(os.environ["LOCALAPPDATA"]) / "PneumoconiosisAnnotator"
    return Path(root) / "local-data"


def model_directory(root):
    return Path(root) / ("models" if packaged(root) else "local-data/models")


def python_executable(root):
    root = Path(root)
    if packaged(root):
        return root / "runtime/python/python.exe"
    return root / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def worker_command(root, mode, job=None):
    import json
    root = Path(root)
    python = python_executable(root)
    if not python.is_file():
        raise RuntimeError("后台运行环境缺失，请修复安装或运行环境准备脚本。")
    command = [str(python), "-I", "-B", "-X", "utf8", "-u", str(root / "scripts/feature_worker.py"), mode]
    if job is not None:
        command.append(json.dumps(job, ensure_ascii=True))
    return command
