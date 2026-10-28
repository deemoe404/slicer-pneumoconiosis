"""Build the offline Windows installer from verified inputs and a prepared environment."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "PneumoconiosisAnnotator"))
from PneumoconiosisLib.core import file_sha256, utc_now
from PneumoconiosisLib.model_config import WEIGHTS_FILE, WEIGHTS_SHA256, WEIGHTS_URL

INPUTS = json.loads((ROOT / "packaging/windows/inputs.json").read_text(encoding="utf-8"))
DOWNLOADS = ROOT / "local-data/windows-downloads"
TOOLS = ROOT / "local-data/windows-tools"
PAYLOAD = ROOT / "local-data/windows-build/PneumoconiosisAnnotator"
OUTPUT = ROOT / "local-data/windows-release"


def checked(command, **kwargs):
    print("Running: " + str(command[0]), flush=True)
    return subprocess.run([str(value) for value in command], check=True, **kwargs)


def download(asset):
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    target = DOWNLOADS / asset["filename"]
    algorithm = "sha512" if "sha512" in asset else "sha256"
    expected = asset[algorithm]

    def digest(path):
        value = hashlib.new(algorithm)
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                value.update(block)
        return value.hexdigest()

    if target.is_file() and digest(target) == expected:
        return target
    temporary = target.with_suffix(".download")
    print("Downloading verified input: " + asset["filename"], flush=True)
    with requests.get(asset["url"], stream=True, timeout=(30, 180)) as response:
        response.raise_for_status()
        with temporary.open("wb") as stream:
            for block in response.iter_content(1024 * 1024):
                stream.write(block)
    if digest(temporary) != expected:
        raise ValueError("Download checksum mismatch: " + asset["filename"])
    os.replace(temporary, target)
    return target


def copy_tree(source, destination):
    shutil.copytree(source, destination, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def clean_environment():
    environment = dict(os.environ)
    for key in ("PYTHONHOME", "PYTHONPATH", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
        environment.pop(key, None)
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONNOUSERSITE"] = "1"
    return environment


def main():
    if sys.platform != "win32" or sys.version_info[:3] != (3, 12, 12):
        raise SystemExit("Build on Windows x64 with the project's Python 3.12.12 environment.")
    if PAYLOAD.exists():
        raise SystemExit("Use a fresh windows-build directory; an existing payload is never overwritten.")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    TOOLS.mkdir(parents=True, exist_ok=True)
    PAYLOAD.mkdir(parents=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    metadata = {"version": INPUTS["version"], "commit": commit, "built_at": utc_now(),
                "windows": INPUTS["windows"],
                "slicer": INPUTS["slicer"], "python": sys.version,
                "model": {"source": WEIGHTS_URL, "sha256": WEIGHTS_SHA256},
                "data_location": "%LOCALAPPDATA%/PneumoconiosisAnnotator"}
    (PAYLOAD / "package.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    slicer_installer = download(INPUTS["slicer"])
    # NSIS requires /D last, without quotes, even when its directory has spaces.
    subprocess.run('"' + str(slicer_installer) + '" /S /D=' + str(PAYLOAD / "Slicer"), check=True, timeout=600)
    if not (PAYLOAD / "Slicer/Slicer.exe").is_file():
        raise RuntimeError("The official Slicer installer did not create the expected application.")
    # The outer installer owns installation/uninstallation of the entire application.
    for path in (PAYLOAD / "Slicer").glob("Uninstall*.exe"):
        path.unlink()

    runtime = PAYLOAD / "runtime/python"
    # Copy the standalone base interpreter, never venv launchers or pyvenv.cfg.
    shutil.copytree(Path(sys.base_prefix), runtime,
                    ignore=shutil.ignore_patterns("site-packages", "Scripts", "__pycache__", "*.pyc"))
    copy_tree(Path(sysconfig.get_path("purelib")), runtime / "Lib/site-packages")
    if (runtime / "pyvenv.cfg").exists():
        raise RuntimeError("The portable interpreter unexpectedly contains venv configuration.")
    # Preserve the app-local redistributable DLLs already included with Slicer.
    for pattern in ("vcruntime*.dll", "msvcp*.dll", "concrt*.dll", "vcomp*.dll"):
        for dll in (PAYLOAD / "Slicer/bin").glob(pattern):
            shutil.copy2(dll, runtime / dll.name)
    python = runtime / "python.exe"
    probe = "import pathlib,sys; p=pathlib.Path(sys.executable).parent.resolve(); assert pathlib.Path(sys.prefix).resolve()==p; assert pathlib.Path(sys.base_prefix).resolve()==p; import torch,torchvision,sounddevice; print(sys.version, torch.__version__, sounddevice.get_portaudio_version())"
    checked([python, "-I", "-B", "-X", "utf8", "-c", probe], env=clean_environment(), timeout=90)
    checked([python, "-I", "-B", "-m", "pip", "check"], env=clean_environment(), timeout=60)

    copy_tree(ROOT / "PneumoconiosisAnnotator", PAYLOAD / "PneumoconiosisAnnotator")
    from build_translations import build as build_translations
    build_translations(PAYLOAD / "Slicer/bin/lrelease.exe", PAYLOAD / "PneumoconiosisAnnotator/Resources/Translations")
    (PAYLOAD / "scripts").mkdir()
    for name in ("feature_worker.py", "start.py"):
        shutil.copy2(ROOT / "scripts" / name, PAYLOAD / "scripts" / name)
    copy_tree(ROOT / "docs", PAYLOAD / "docs")
    for name in ("README.md", "LICENSE", "requirements-runtime.txt", "requirements-test.txt", "requirements-windows-lock.txt"):
        shutil.copy2(ROOT / name, PAYLOAD / name)
    (PAYLOAD / "qa").mkdir()
    shutil.copy2(ROOT / "tests/synthetic_pipeline.py", PAYLOAD / "qa/synthetic_pipeline.py")
    shutil.copy2(ROOT / "packaging/windows/slicer_package_check.py", PAYLOAD / "qa/slicer_package_check.py")
    shutil.copy2(ROOT / "packaging/windows/render_check.py", PAYLOAD / "qa/render_check.py")
    shutil.copy2(ROOT / "packaging/windows/reference_check.py", PAYLOAD / "qa/reference_check.py")
    shutil.copy2(ROOT / "packaging/windows/language_check.py", PAYLOAD / "qa/language_check.py")
    sys.path.insert(0, str(ROOT / "tests"))
    from synthetic_pipeline import create_dicom
    create_dicom(PAYLOAD / "qa/dicom")
    (PAYLOAD / "models").mkdir()
    weights = ROOT / "local-data/models" / WEIGHTS_FILE
    if file_sha256(weights) != WEIGHTS_SHA256:
        raise RuntimeError("Prepared model checksum mismatch.")
    shutil.copy2(weights, PAYLOAD / "models" / WEIGHTS_FILE)

    metadata["dependencies"] = [{"name": d.metadata["Name"], "version": d.version,
                                  "license": d.metadata.get("License-Expression", d.metadata.get("License", ""))}
                                 for d in sorted(importlib.metadata.distributions(), key=lambda d: d.metadata["Name"].lower())]
    notices = "Third-party notices\n\nThe project BSD-3-Clause license covers original code and documentation only.\n"
    notices += "Slicer and its component licenses are retained in Slicer/.\nPython's license is retained in runtime/python/.\n"
    notices += "Chinese translation sources, upstream URLs/checksums and licenses are retained in PneumoconiosisAnnotator/Resources/Translations/.\n"
    notices += "Python dependency license files are retained in runtime/python/Lib/site-packages, including *.dist-info/licenses.\n"
    notices += "Model source: " + WEIGHTS_URL + "\nModel SHA256: " + WEIGHTS_SHA256 + "\n\n"
    notices += "\n".join(d["name"] + " " + d["version"] + "\n" + d["license"] for d in metadata["dependencies"])
    (PAYLOAD / "THIRD-PARTY-NOTICES.txt").write_text(notices, encoding="utf-8")

    compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    checked([compiler, "/nologo", "/target:winexe", "/platform:x64", "/codepage:65001", "/reference:System.Windows.Forms.dll",
             "/out:" + str(PAYLOAD / "PneumoconiosisAnnotator.exe"), ROOT / "packaging/windows/Launcher.cs"], timeout=60)
    metadata["files"] = {str(path.relative_to(PAYLOAD)).replace("\\", "/"): file_sha256(path)
                         for path in sorted(PAYLOAD.rglob("*")) if path.is_file() and path != PAYLOAD / "package.json"}
    (PAYLOAD / "package.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    inno = TOOLS / "inno"
    checked([download(INPUTS["inno"]), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", "/DIR=" + str(inno)], timeout=180)
    checked([inno / "ISCC.exe", "/DPayload=" + str(PAYLOAD), "/DOutput=" + str(OUTPUT),
             "/DAppVersion=" + INPUTS["version"], "/DBuildId=" + commit[:7],
             "/DMinimumWindowsVersion=" + INPUTS["windows"]["minimum_version"],
             "/DMinimumWindowsRelease=" + INPUTS["windows"]["minimum_release"],
             ROOT / "packaging/windows/installer.iss"], timeout=1200)
    installer = next(OUTPUT.glob("*-Setup.exe"))
    if installer.stat().st_size >= 2 * 1024 ** 3:
        raise RuntimeError("Installer exceeds the release asset limit.")
    (OUTPUT / (installer.name + ".sha256")).write_text(file_sha256(installer) + "  " + installer.name + "\n", encoding="ascii")
    (OUTPUT / "build-manifest.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Built offline installer: " + str(installer), flush=True)


if __name__ == "__main__":
    main()
