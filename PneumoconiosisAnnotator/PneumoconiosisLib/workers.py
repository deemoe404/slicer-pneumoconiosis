"""Qt-managed isolated workers; no model packages are injected into Slicer's Python."""
from __future__ import annotations

import json
from pathlib import Path

import qt


class Worker:
    def __init__(self, root, mode, event, finished=None, job=None):
        self.root, self.mode = Path(root), mode
        self.event, self.finished = event, finished
        self.buffer = ""
        self.stderr = ""
        self.closing = False
        self.process = qt.QProcess()
        self.process.setWorkingDirectory(str(self.root))
        environment = qt.QProcessEnvironment.systemEnvironment()
        for name in ("PYTHONHOME", "PYTHONPATH", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH"):
            environment.remove(name)
        environment.insert("PYTHONNOUSERSITE", "1")
        environment.insert("PYTHONUNBUFFERED", "1")
        self.process.setProcessEnvironment(environment)
        self.process.connect("readyReadStandardOutput()", self.read)
        self.process.connect("readyReadStandardError()", self.read_errors)
        self.process.connect("finished(int,QProcess::ExitStatus)", self.ended)
        python = self.root / ".venv" / ("Scripts/python.exe" if __import__("os").name == "nt" else "bin/python")
        if not python.exists():
            raise RuntimeError("功能组件尚未准备，请先运行项目的运行环境准备脚本。")
        arguments = [str(self.root / "scripts" / "feature_worker.py"), mode]
        if job is not None:
            arguments.append(json.dumps(job, ensure_ascii=True))
        self.process.start(str(python), arguments)
        if not self.process.waitForStarted(5000):
            raise RuntimeError("后台功能启动失败：" + self.process.errorString())

    def read(self):
        self.buffer += self.process.readAllStandardOutput().data().decode("ascii", errors="replace")
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            if line.strip() and not self.closing:
                try:
                    value = json.loads(line)
                except ValueError:
                    self.stderr = (self.stderr + line)[-4000:]
                    continue
                self.event(value)

    def read_errors(self):
        self.stderr = (self.stderr + self.process.readAllStandardError().data().decode("utf-8", errors="replace"))[-4000:]

    def ended(self, code, _status):
        self.read()
        self.read_errors()
        if self.finished and not self.closing:
            self.finished(code, self.stderr)

    def send(self, value):
        if self.process.state() != qt.QProcess.Running:
            raise RuntimeError("后台进程已停止，请重试。")
        self.process.write((json.dumps(value, ensure_ascii=True) + "\n").encode("ascii"))

    def stop_recording(self):
        if self.process.state() != qt.QProcess.NotRunning:
            self.process.write(b"stop\n")
            self.process.closeWriteChannel()
            if not self.process.waitForFinished(5000):
                return False
        self.read()
        return True

    def close(self):
        self.closing = True
        self.process.closeWriteChannel()
        if self.process.state() != qt.QProcess.NotRunning and not self.process.waitForFinished(1000):
            self.process.terminate()
            if not self.process.waitForFinished(1500):
                self.process.kill()
                self.process.waitForFinished(1500)
