"""Prepare only this project's Python environment and verified public model weights."""
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]
if sys.version_info[:2] != (3, 12):
    raise SystemExit("请用 Python 3.12 运行此准备脚本（本项目使用 mise 固定版本）。")
environment = ROOT / ".venv"
python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
if not python.exists():
    venv.EnvBuilder(with_pip=True).create(environment)
constraints = ["-c", str(ROOT / "requirements-windows-lock.txt")] if sys.platform == "win32" else []
if sys.platform in ("win32", "linux"):
    # Fetch CPU wheels here; resolve all other dependencies from PyPI below.
    subprocess.run([str(python), "-m", "pip", "install", "torch==2.14.1", "torchvision==0.29.1",
                    "--index-url", "https://download.pytorch.org/whl/cpu", "--no-deps", *constraints], check=True)
subprocess.run([str(python), "-m", "pip", "install", "-r", str(ROOT / "requirements-runtime.txt"), *constraints], check=True)
subprocess.run([str(python), str(ROOT / "scripts" / "prepare_markups.py")], check=True)
subprocess.run([str(python), str(ROOT / "scripts" / "prepare_model.py")], check=True)
