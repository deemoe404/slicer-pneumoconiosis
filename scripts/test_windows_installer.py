"""Install, exercise offline, reinstall and uninstall the generated Windows package."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import traceback
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_windows_installer import INPUTS, OUTPUT, ROOT, TOOLS, checked, clean_environment, download

sys.path.insert(0, str(ROOT / "PneumoconiosisAnnotator"))
from PneumoconiosisLib.core import AnnotationStore, file_sha256

OUT = ROOT / "local-data/qa/windows-installer"
REPORT = {"status": "failed", "checks": []}


def check(value, message):
    if not value:
        raise AssertionError(message)
    REPORT["checks"].append(message)
    print("PASS: " + message, flush=True)


def run():
    installer = next(OUTPUT.glob("*-Setup.exe"))
    metadata = json.loads((OUTPUT / "build-manifest.json").read_text(encoding="utf-8"))
    install = Path(os.environ["ProgramFiles"]) / ("PneumoconiosisAnnotator-CI-" + metadata["commit"][:7])
    if install.exists():
        raise RuntimeError("Refusing to reuse an existing test installation.")
    REPORT.update(commit=metadata["commit"], installer=installer.name, sha256=file_sha256(installer),
                  installation=str(install), graphics="Mesa llvmpipe (test only; not shipped)")
    command = [installer, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", "/DIR=" + str(install)]
    checked(command + ["/LOG=" + str(OUT / "install.log")], timeout=300)
    check((install / "PneumoconiosisAnnotator.exe").is_file(), "offline installer creates the application launcher")
    for relative, digest in metadata["files"].items():
        if file_sha256(install / relative) != digest:
            raise AssertionError("Installed file differs from payload: " + relative)
    check(True, "every installed payload file matches its recorded SHA256")
    check(not (install / "runtime/python/pyvenv.cfg").exists(), "runtime has no dependency on the build machine's venv")
    environment = clean_environment()
    environment["PATH"] = os.pathsep.join((str(Path(os.environ["WINDIR"]) / "System32"), os.environ["WINDIR"]))
    environment.update(GALLIUM_DRIVER="llvmpipe", MESA_GL_VERSION_OVERRIDE="4.5", QT_OPENGL="software")
    python = install / "runtime/python/python.exe"
    probe_prefix = "from pathlib import Path; import sys,torch,numpy; p=Path(sys.executable).parent.resolve(); assert Path(sys.prefix).resolve()==p and Path(sys.base_prefix).resolve()==p; assert all(Path(m.__file__).resolve().is_relative_to(p) for m in (torch,numpy))"
    checked([python, "-I", "-B", "-X", "utf8", "-c", probe_prefix], env=environment, timeout=90)
    check(True, "relocated interpreter and binary dependencies resolve entirely within the installed runtime")
    firewall = []
    mesa_files = []
    try:
        checked(["netsh", "advfirewall", "set", "currentprofile", "state", "on"], timeout=30)
        for index, executable in enumerate((python, install / "Slicer/Slicer.exe", install / "Slicer/bin/SlicerApp-real.exe")):
            name = "PneumoOfflineCheck-" + metadata["commit"][:7] + "-" + str(index)
            checked(["netsh", "advfirewall", "firewall", "add", "rule", "name=" + name, "dir=out", "action=block", "program=" + str(executable)], timeout=30)
            firewall.append(name)
        probe = "import requests\ntry:\n requests.get('https://github.com/',timeout=(4,4))\nexcept requests.RequestException:\n print('Installed runtime has no outbound network access')\nelse:\n raise SystemExit('Offline test firewall did not block the installed runtime')"
        checked([python, "-I", "-B", "-X", "utf8", "-c", probe], env=environment, timeout=20)
        check(True, "network probe confirms outbound access is blocked for the installed runtime")
        checked([python, "-I", "-B", "-X", "utf8", install / "qa/synthetic_pipeline.py", "--output", OUT / "backend"], env=environment, timeout=240)
        backend = json.loads((OUT / "backend/report.json").read_text(encoding="utf-8"))
        check(backend["status"] == "passed", "installed portable backend infers, persists and exports with outbound traffic blocked")
        REPORT["backend"] = backend

        mesa = TOOLS / "mesa"
        mesa.mkdir(parents=True, exist_ok=True)
        checked(["7z", "x", download(INPUTS["mesa_test_only"]), "-o" + str(mesa), "-y"], timeout=90, stdout=subprocess.DEVNULL)
        directory = next(p.parent for p in mesa.rglob("opengl32.dll") if "x64" in p.parts)
        for source in directory.glob("*.dll"):
            target = install / "Slicer/bin" / source.name
            if target.exists():
                raise RuntimeError("Test graphics DLL would overwrite a shipped DLL: " + str(target))
            shutil.copy2(source, target)
            mesa_files.append(target)
        process = subprocess.Popen([str(install / "PneumoconiosisAnnotator.exe"), "--self-test", str(OUT / "gui")], env=environment)
        try:
            exit_code = process.wait(timeout=240)
        except subprocess.TimeoutExpired:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], check=False)
            raise
        gui_path = OUT / "gui/gui-report.json"
        if exit_code or not gui_path.exists():
            raise RuntimeError("Installed Slicer GUI failed; inspect application-data/logs and gui-report.json")
        gui = json.loads(gui_path.read_text(encoding="utf-8"))
        check(gui["status"] == "passed", "installed launcher opens the full GUI and completes its offline model/export checks")
        REPORT["gui"] = gui

        project = Path(gui["project"])
        expected = gui["record"]
        def restored():
            store = AnnotationStore(project)
            try:
                return store.get(expected["case_id"], expected["reader_id"])
            finally:
                store.close()
        check(restored() == expected, "GUI annotation survives application shutdown")
        checked(command + ["/LOG=" + str(OUT / "reinstall.log")], timeout=300)
        check(restored() == expected, "reinstall preserves the external annotation project")
        for path in mesa_files:
            path.unlink(missing_ok=True)
        mesa_files.clear()
        checked([install / "unins000.exe", "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/LOG=" + str(OUT / "uninstall.log")], timeout=180)
        check(not (install / "PneumoconiosisAnnotator.exe").exists() and restored() == expected,
              "uninstall removes the application and preserves the annotation project")
        REPORT["status"] = "passed"
    finally:
        for name in firewall:
            subprocess.run(["netsh", "advfirewall", "firewall", "delete", "rule", "name=" + name], check=False, capture_output=True)


def main():
    if sys.platform != "win32":
        raise SystemExit("Run installer verification on Windows x64.")
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        run()
    except Exception:
        REPORT["error"] = traceback.format_exc()
        raise
    finally:
        (OUT / "installer-report.json").write_text(json.dumps(REPORT, ensure_ascii=False, indent=2), encoding="utf-8")
        with zipfile.ZipFile(OUTPUT / "windows-installer-checks.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in OUT.rglob("*"):
                if path.is_file():
                    archive.write(path, path.relative_to(OUT))
        summary = "## Windows offline installer: " + REPORT["status"] + "\n\n" + "\n".join("- " + item for item in REPORT["checks"])
        summary += "\n\nGUI smoke checks use a synthetic image and test-only Mesa software rendering; physical GPU and microphone acceptance remain separate.\n"
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
                stream.write(summary)


if __name__ == "__main__":
    main()
