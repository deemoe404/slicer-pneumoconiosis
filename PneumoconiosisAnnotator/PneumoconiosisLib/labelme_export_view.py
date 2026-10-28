"""Independent Labelme export action backed by the existing isolated worker."""
import json
from pathlib import Path
import tempfile

import qt
import slicer

from .core import atomic_write
from .workers import Worker
from .directory_picker import choose_directory


class LabelmeExport:
    def __init__(self, widget, root):
        self.w, self.root = widget, root
        self.worker = self.temporary = self.progress = None
        self.last_result = self.last_error = None
        self.button = qt.QPushButton("导出 LabelMe")
        self.button.setObjectName("PneumoLabelmeExportButton")
        self.button.setToolTip("按读者导出胸片 PNG 和椭圆 / 多边形标注，可在 LabelMe 中打开。")
        self.button.connect("clicked()", self.choose)

    def choose(self):
        if self.w.store is None or self.worker is not None:
            return
        directory = choose_directory(slicer.util.mainWindow(), "选择 LabelMe 导出目录",
                                     self.w.store.directory, "PneumoLabelmeDirectoryDialog", create=True)
        if directory:
            try:
                self.start(directory)
            except Exception as exc:
                self.close()
                self.last_error = str(exc)
                self.w.showError("LabelMe 导出失败：" + str(exc))

    def start(self, directory):
        if self.worker is not None:
            raise RuntimeError("LabelMe 正在导出，请稍候。")
        with self.w.busy.during("正在保存并准备导出…"):
            if self.w.store is None or not self.w.prepareLeave():
                raise RuntimeError("保存失败，已停止导出。")
            snapshot = self.w.store.export_snapshot([c["case_id"] for c in self.w.cases])
            for package in snapshot["packages"]:
                del package["history"]
            self.last_result = self.last_error = None
            self.temporary = tempfile.TemporaryDirectory(prefix="pneumo-labelme-")
            path = Path(self.temporary.name) / "snapshot.json"
            atomic_write(path, json.dumps(snapshot, ensure_ascii=False, allow_nan=False))
        self.progress = self.w.busy
        self.progress.begin("正在导出 LabelMe…", cancel=self.cancel)
        try:
            self.worker = Worker(self.root, "labelme", self.event, self.finished,
                                 job={"snapshot": str(path), "destination": str(directory)})
        except Exception:
            self.close()
            raise
        self.update_controls()

    def event(self, value):
        if value["event"] == "progress" and self.progress is not None:
            self.progress.set_progress(value["current"], value["total"])
        elif value["event"] == "exported":
            self.last_result = value
        elif value["event"] == "error":
            self.last_error = value["message"]

    def finished(self, code, stderr):
        self.close()
        if code != 0 or self.last_result is None:
            self.last_error = self.last_error or stderr.strip() or "后台进程未完成导出。"
            self.w.showError("LabelMe 导出失败：" + self.last_error)
            return
        result = self.last_result
        message = f"LabelMe 已导出：{result['reader_count']} 位读者，{result['case_count']} 张胸片。"
        if result["skipped_count"]:
            message += f" {result['skipped_count']} 个未完成轮廓未转换，详见 export-report.json。"
        self.w.setSaveStatus(message)
        self.w.saveLabel.setStyleSheet("color:#207544; margin:6px 0;")
        self.w.saveLabel.show()
        qt.QDesktopServices.openUrl(qt.QUrl.fromLocalFile(result["directory"]))

    def cancel(self):
        if self.worker is not None:
            self.worker.closing = True
            self.worker.process.kill()
            self.worker.process.waitForFinished(1500)
        self.close()
        self.w.setSaveStatus("LabelMe 导出已取消。")

    def close(self):
        if self.worker is not None:
            self.worker.close()
            self.worker = None
        if self.progress is not None:
            self.progress.end()
            self.progress = None
        if self.temporary is not None:
            self.temporary.cleanup()
            self.temporary = None
        self.update_controls()

    def update_controls(self):
        self.button.setEnabled(self.w.currentIndex >= 0 and self.w.volume is not None and self.worker is None)
