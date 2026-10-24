"""Simple single-frame chest-radiograph reader for independent six-zone research ratings."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import sys
from pathlib import Path

import qt
import slicer
import vtk
from slicer.ScriptedLoadableModule import ScriptedLoadableModule, ScriptedLoadableModuleWidget

MODULE_DIR = Path(__file__).resolve().parent
ROOT = MODULE_DIR.parent
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))
from PneumoconiosisLib.core import (
    AnnotationStore, GRADES, ZONES, ZONE_NAMES, atomic_write, file_sha256, scan_cases, validate_complete, validate_reader,
)


class PneumoconiosisAnnotator(ScriptedLoadableModule):
    def __init__(self, parent):
        super().__init__(parent)
        self.parent.title = "尘肺胸片标注"
        self.parent.categories = ["Research"]
        self.parent.dependencies = ["DICOM", "Volumes", "Colors"]
        self.parent.contributors = ["SUEP CT Research"]
        self.parent.helpText = "六肺区小阴影密集度独立读片。原图只读，标注自动保存到本地。"
        self.parent.acknowledgementText = "基于 3D Slicer 的科研标注模块。"


class CloseGuard(qt.QWidget):
    def __init__(self, widget):
        super().__init__()
        self.widget = widget

    def eventFilter(self, obj, event):
        if event.type() == qt.QEvent.Close and not self.widget.flushSave():
            event.ignore()
            return True
        return False


class PictureWheelGuard(qt.QWidget):
    """In a single-frame radiograph, wheel input zooms instead of leaving the image plane."""
    def __init__(self, widget):
        super().__init__()
        self.widget = widget

    def eventFilter(self, obj, event):
        if event.type() != qt.QEvent.Wheel or not self.widget.entered or self.widget.volume is None:
            return False
        view = slicer.app.layoutManager().sliceWidget("Red").sliceView()
        if not obj.isWidgetType() or not (obj == view or view.isAncestorOf(obj)):
            return False
        delta = event.angleDelta().y()
        if not delta:
            return False
        node = slicer.app.layoutManager().sliceWidget("Red").mrmlSliceNode()
        fov = node.GetFieldOfView()
        factor = 2.0 ** (max(-1200, min(1200, delta)) / 600.0)
        node.SetFieldOfView(fov[0] / factor, fov[1] / factor, fov[2])
        node.UpdateMatrices()
        event.accept()
        return True


class PneumoconiosisAnnotatorWidget(ScriptedLoadableModuleWidget):
    def setup(self):
        super().setup()
        self.store = None
        self.cases = []
        self.currentIndex = -1
        self.currentReader = "reader-01"
        self.currentRecord = None
        self.volume = None
        self.dirty = False
        self.updating = False
        self.entered = False
        self.dataRoot = ROOT / "local-data"
        self.saveTimer = qt.QTimer()
        self.saveTimer.setSingleShot(True)
        self.saveTimer.setInterval(450)
        self.saveTimer.connect("timeout()", self.flushSave)

        self.layout.setContentsMargins(12, 8, 12, 8)
        heading = qt.QLabel("尘肺胸片标注")
        heading.setStyleSheet("font-size:22px; font-weight:600; margin-bottom:4px;")
        self.layout.addWidget(heading)
        subtitle = qt.QLabel("六肺区小阴影密集度 · GBZ 70—2015")
        subtitle.setStyleSheet("color:#7d8899; margin-bottom:10px;")
        self.layout.addWidget(subtitle)

        top = qt.QHBoxLayout()
        self.openButton = qt.QPushButton("打开 DICOM 目录")
        self.openButton.connect("clicked()", self.chooseDirectory)
        top.addWidget(self.openButton)
        self.exportButton = qt.QPushButton("导出结果")
        self.exportButton.connect("clicked()", self.chooseExport)
        top.addWidget(self.exportButton)
        self.layout.addLayout(top)

        reader_row = qt.QHBoxLayout()
        reader_row.addWidget(qt.QLabel("读者编号"))
        self.readerEdit = qt.QLineEdit(self.currentReader)
        self.readerEdit.setToolTip("每位读者使用自己的编号；切换编号会读取该读者的独立记录。")
        self.readerEdit.connect("editingFinished()", self.changeReader)
        reader_row.addWidget(self.readerEdit)
        self.layout.addLayout(reader_row)

        self.caseCombo = qt.QComboBox()
        self.caseCombo.setMinimumContentsLength(20)
        self.caseCombo.connect("currentIndexChanged(int)", self.selectCase)
        self.layout.addWidget(self.caseCombo)
        self.progressLabel = qt.QLabel("请选择包含单帧胸片的目录")
        self.progressLabel.setStyleSheet("color:#7d8899;")
        self.layout.addWidget(self.progressLabel)

        nav = qt.QHBoxLayout()
        self.previousButton = qt.QPushButton("← 上一例")
        self.nextButton = qt.QPushButton("下一例 →")
        self.previousButton.connect("clicked()", lambda: self.selectCase(self.currentIndex - 1))
        self.nextButton.connect("clicked()", lambda: self.selectCase(self.currentIndex + 1))
        nav.addWidget(self.previousButton)
        nav.addWidget(self.nextButton)
        self.layout.addLayout(nav)

        self.orientationLabel = qt.QLabel()
        self.orientationLabel.setWordWrap(True)
        self.orientationLabel.setStyleSheet("color:#c79238; font-size:11px; margin:6px 0;")
        self.layout.addWidget(self.orientationLabel)

        ratings = qt.QGroupBox("六肺区评分")
        grid = qt.QGridLayout(ratings)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(10)
        grid.addWidget(qt.QLabel("患者右肺"), 0, 0, 1, 2)
        grid.addWidget(qt.QLabel("患者左肺"), 0, 2, 1, 2)
        self.gradeCombos = {}
        for row, (right, left) in enumerate(zip(ZONES[:3], ZONES[3:]), 1):
            for column, zone in ((0, right), (2, left)):
                grid.addWidget(qt.QLabel(ZONE_NAMES[zone]), row, column)
                combo = qt.QComboBox()
                combo.addItem("— 未评 —", "unread")
                for grade in GRADES:
                    combo.addItem(grade, grade)
                combo.addItem("不可评价", "not_scorable")
                combo.setMinimumWidth(105)
                combo.setMaxVisibleItems(14)
                combo.connect("currentIndexChanged(int)", self.ratingChanged)
                self.gradeCombos[zone] = combo
                grid.addWidget(combo, row, column + 1)
        self.layout.addWidget(ratings)

        self.layout.addWidget(qt.QLabel("备注 / 不可评价原因"))
        self.noteEdit = qt.QPlainTextEdit()
        self.noteEdit.setMaximumHeight(72)
        self.noteEdit.setPlaceholderText("不可评价时，请注明肺区和原因。")
        self.noteEdit.connect("textChanged()", self.noteChanged)
        self.layout.addWidget(self.noteEdit)

        self.saveLabel = qt.QLabel("尚未打开病例")
        self.saveLabel.setWordWrap(True)
        self.layout.addWidget(self.saveLabel)
        self.retryButton = qt.QPushButton("重试保存")
        self.retryButton.connect("clicked()", self.flushSave)
        self.retryButton.hide()
        self.layout.addWidget(self.retryButton)
        self.completeButton = qt.QPushButton("完成并下一例")
        self.completeButton.setMinimumHeight(34)
        self.completeButton.setStyleSheet("font-weight:600;")
        self.completeButton.connect("clicked()", self.completeCase)
        self.layout.addWidget(self.completeButton)

        image_row = qt.QHBoxLayout()
        self.fitButton = qt.QPushButton("适应窗口")
        self.fitButton.connect("clicked()", self.fitImage)
        self.resetButton = qt.QPushButton("原始灰度")
        self.resetButton.connect("clicked()", self.resetDisplay)
        image_row.addWidget(self.fitButton)
        image_row.addWidget(self.resetButton)
        self.layout.addLayout(image_row)

        tip = qt.QLabel("未评 ≠ 0 级；不可评价单独记录。\n六区为读片分区，评分需参照标准片。")
        tip.setStyleSheet("color:#7d8899; font-size:11px; margin-top:8px;")
        tip.setWordWrap(True)
        self.layout.addWidget(tip)
        self.locationLabel = qt.QLabel()
        self.locationLabel.setWordWrap(True)
        self.locationLabel.setStyleSheet("color:#7d8899; font-size:10px;")
        self.layout.addWidget(self.locationLabel)
        self.layout.addStretch(1)
        self.closeGuard = CloseGuard(self)
        slicer.util.mainWindow().installEventFilter(self.closeGuard)
        self.wheelGuard = PictureWheelGuard(self)
        slicer.app.installEventFilter(self.wheelGuard)
        self.sceneCloseObserver = slicer.mrmlScene.AddObserver(slicer.vtkMRMLScene.StartCloseEvent, self.onSceneClose)
        self.updateControls()

    def enter(self):
        self.entered = True
        slicer.app.layoutManager().setLayout(slicer.vtkMRMLLayoutNode.SlicerLayoutOneUpRedSliceView)
        slicer.util.setModuleHelpSectionVisible(False)
        if hasattr(slicer.modules, "DataProbeInstance"):
            annotations = slicer.modules.DataProbeInstance.infoWidget.sliceAnnotations
            annotations.sliceViewAnnotationsEnabled = False
            annotations.updateSliceViewFromGUI()
        red = slicer.app.layoutManager().sliceWidget("Red")
        red.sliceView().setBackgroundColor(qt.QColor("#15191f"))
        red.sliceController().hide()

    def exit(self):
        self.flushSave()
        self.entered = False

    def cleanup(self):
        self.saveTimer.stop()
        self.flushSave()
        if getattr(self, "closeGuard", None):
            slicer.util.mainWindow().removeEventFilter(self.closeGuard)
        if getattr(self, "sceneCloseObserver", None):
            slicer.mrmlScene.RemoveObserver(self.sceneCloseObserver)
        if getattr(self, "wheelGuard", None):
            slicer.app.removeEventFilter(self.wheelGuard)
        if self.store:
            self.store.close()
            self.store = None

    def onSceneClose(self, *_):
        saved = self.flushSave()
        self.volume = None
        if saved:
            self.currentRecord = None
            self.currentIndex = -1
        self.updateControls()

    def chooseDirectory(self):
        folder = qt.QFileDialog.getExistingDirectory(slicer.util.mainWindow(), "选择 DICOM 病例目录", str(ROOT.parent))
        if folder:
            try:
                self.openDirectory(folder)
            except Exception as exc:
                self.showError("打开失败：" + str(exc))

    def openDirectory(self, source, projectDirectory=None, reader=None):
        if not self.flushSave():
            return False
        source = Path(source).resolve()
        cases, skipped = scan_cases(source)
        if projectDirectory is None:
            key = hashlib.sha256(str(source).encode()).hexdigest()[:16]
            projectDirectory = self.dataRoot / "projects" / key
        new_store = AnnotationStore(projectDirectory)
        try:
            new_store.import_cases(cases)
            new_reader = validate_reader(reader or new_store.preference("reader", "reader-01"))
        except Exception:
            new_store.close()
            raise
        if self.store:
            self.store.close()
        self.store, self.cases = new_store, cases
        self.currentReader = new_reader
        self.readerEdit.setText(new_reader)
        self.currentIndex, self.currentRecord = -1, None
        self.dirty = False
        self.updating = True
        self.caseCombo.clear()
        for index, case in enumerate(cases):
            self.caseCombo.addItem(f"病例 {index + 1:03d} · {case['case_id'][-6:]}")
        self.updating = False
        self.locationLabel.setText("仅保存到本机 · 原始 DICOM 保持只读")
        self.locationLabel.setToolTip(str(new_store.path))
        last_id = new_store.preference("last_case:" + new_reader)
        index = next((i for i, case in enumerate(cases) if case["case_id"] == last_id), 0)
        opened = self.selectCase(index)
        if skipped:
            self.orientationLabel.setText(self.orientationLabel.text + f"\n跳过 {skipped} 个非目标或重复文件。")
        if opened:
            self.dataRoot.mkdir(parents=True, exist_ok=True)
            atomic_write(self.dataRoot / "last-project.json", json.dumps({"source": str(source), "project": str(new_store.directory)}, ensure_ascii=False))
        return opened

    def _loadVolume(self, case):
        from DICOMLib import DICOMUtils
        import ctk
        import numpy as np
        import pydicom

        if file_sha256(case["file_path"]) != case["source_sha256"]:
            raise ValueError("原始图像内容已变化，请重新核对数据。")
        previous_selection = slicer.app.applicationLogic().GetSelectionNode().GetActiveVolumeID()
        created = None
        try:
            with DICOMUtils.TemporaryDICOMDatabase(str(self.store.directory / "dicom-cache")) as db:
                indexer = ctk.ctkDICOMIndexer()
                indexer.addFile(db, case["file_path"], False)
                indexer.waitForImportFinished()
                plugin = slicer.modules.dicomPlugins["DICOMScalarVolumePlugin"]()
                loadables = plugin.examineForImport([[case["file_path"]]])
                if not loadables:
                    raise ValueError("Slicer 无法读取这张胸片。")
                loadable = max(loadables, key=lambda x: x.confidence)
                loadable.name = case["case_id"]
                created = plugin.load(loadable)
                if created is None or created.GetImageData() is None:
                    raise ValueError("胸片像素未成功加载。")
            dimensions = created.GetImageData().GetDimensions()
            if list(dimensions) != [case["columns"], case["rows"], 1]:
                raise ValueError("读取尺寸与原始胸片不一致。")
            created.SetName(case["case_id"])
            # GDCM may already invert MONOCHROME1. Inspect pixels before selecting a display LUT.
            ds = pydicom.dcmread(case["file_path"])
            display = created.GetDisplayNode()
            display.AutoWindowLevelOff()
            if str(ds.PhotometricInterpretation) == "MONOCHROME1":
                raw = ds.pixel_array.astype(np.float64)
                raw = raw * float(ds.get("RescaleSlope", 1)) + float(ds.get("RescaleIntercept", 0))
                loaded = slicer.util.arrayFromVolume(created)[0].astype(np.float64)
                if np.allclose(loaded, raw):
                    display.SetAndObserveColorNodeID("vtkMRMLColorTableNodeInvertedGrey")
                elif np.allclose(loaded + raw, (loaded + raw).flat[0]):
                    display.SetAndObserveColorNodeID("vtkMRMLColorTableNodeGrey")
                else:
                    raise ValueError("无法确认 MONOCHROME1 灰度映射，已停止切换病例。")
            self.defaultDisplay = {"window": display.GetWindow(), "level": display.GetLevel(), "color": display.GetColorNodeID()}
            return created
        except Exception:
            if created is not None:
                slicer.mrmlScene.RemoveNode(created)
            if previous_selection:
                slicer.util.setSliceViewerLayers(background=previous_selection)
            raise

    def _orientImage(self):
        """Display stored columns left-to-right and stored rows top-to-bottom, without guessing anatomy."""
        import numpy as np
        matrix = vtk.vtkMatrix4x4()
        self.volume.GetIJKToRASMatrix(matrix)
        x = np.array([matrix.GetElement(r, 0) for r in range(3)])
        y = -np.array([matrix.GetElement(r, 1) for r in range(3)])
        x /= np.linalg.norm(x)
        y /= np.linalg.norm(y)
        n = np.cross(x, y)
        dimensions = self.volume.GetImageData().GetDimensions()
        center = matrix.MultiplyPoint([(v - 1) / 2 for v in dimensions] + [1])
        pose = vtk.vtkMatrix4x4()
        pose.Identity()
        for row in range(3):
            for col, vector in enumerate((x, y, n)):
                pose.SetElement(row, col, float(vector[row]))
            pose.SetElement(row, 3, center[row])
        node = slicer.app.layoutManager().sliceWidget("Red").mrmlSliceNode()
        node.GetSliceToRAS().DeepCopy(pose)
        node.UpdateMatrices()
        self.fitImage()

    def selectCase(self, index):
        if self.updating:
            return False
        if index < 0 or index >= len(self.cases):
            return False
        if index == self.currentIndex:
            return True
        if not self.flushSave():
            self._resetCombo()
            return False
        try:
            new_volume = self._loadVolume(self.cases[index])
        except Exception as exc:
            self._resetCombo()
            self.showError("读取失败：" + str(exc))
            return False
        old_volume = self.volume
        self.volume = new_volume
        self.currentIndex = index
        self.currentRecord = self.store.get(self.cases[index]["case_id"], self.currentReader)
        slicer.util.setSliceViewerLayers(background=new_volume)
        self._orientImage()
        self._restoreDisplay(self.currentRecord.get("display", {}))
        if old_volume:
            sh = slicer.vtkMRMLSubjectHierarchyNode.GetSubjectHierarchyNode(slicer.mrmlScene)
            item = sh.GetItemByDataNode(old_volume)
            if item:
                sh.RemoveItem(item)
            else:
                slicer.mrmlScene.RemoveNode(old_volume)
        self._populate()
        self.store.set_preference("last_case:" + self.currentReader, self.cases[index]["case_id"])
        self.updateControls()
        return True

    def _resetCombo(self):
        self.updating = True
        self.caseCombo.setCurrentIndex(self.currentIndex)
        self.updating = False

    def _populate(self):
        self.updating = True
        for zone, combo in self.gradeCombos.items():
            value = self.currentRecord["zones"][zone]
            token = value["grade"] if value["state"] == "scored" else value["state"]
            combo.setCurrentIndex(combo.findData(token))
        self.noteEdit.setPlainText(self.currentRecord["note"])
        self.caseCombo.setCurrentIndex(self.currentIndex)
        self.updating = False
        self.dirty = False
        known = self.cases[self.currentIndex]["orientation_known"]
        self.orientationLabel.setText("" if known else "方向信息不完整：按原始图像方向显示，请核对片内侧别标记。")
        self.setSaveStatus("已完成 · 已保存" if self.currentRecord["status"] == "complete" else ("草稿已恢复" if self.currentRecord["updated_at"] else "未评 · 修改后自动保存"))

    def _zonesFromUI(self):
        values = {}
        for zone, combo in self.gradeCombos.items():
            token = combo.currentData
            values[zone] = {"state": "scored" if token in GRADES else token, "grade": token if token in GRADES else None}
        return values

    def ratingChanged(self, *_):
        if self.updating or self.currentRecord is None:
            return
        self.currentRecord["status"] = "draft"
        self.dirty = True
        self.flushSave()

    def noteChanged(self):
        if self.updating or self.currentRecord is None:
            return
        self.currentRecord["status"] = "draft"
        self.dirty = True
        self.setSaveStatus("正在保存…")
        self.saveTimer.start()

    def _captureDisplay(self):
        if self.volume is None or self.volume.GetDisplayNode() is None:
            return self.currentRecord.get("display", {}) if self.currentRecord else {}
        display = self.volume.GetDisplayNode()
        node = slicer.app.layoutManager().sliceWidget("Red").mrmlSliceNode()
        return {"window": display.GetWindow(), "level": display.GetLevel(), "color": display.GetColorNodeID(),
                "field_of_view": list(node.GetFieldOfView()), "origin": list(node.GetXYZOrigin())}

    def _restoreDisplay(self, state):
        if not state:
            return
        display = self.volume.GetDisplayNode()
        if all(math.isfinite(float(state.get(k, float("nan")))) for k in ("window", "level")):
            display.AutoWindowLevelOff()
            display.SetWindowLevel(float(state["window"]), float(state["level"]))
        if state.get("color") and slicer.mrmlScene.GetNodeByID(state["color"]):
            display.SetAndObserveColorNodeID(state["color"])
        node = slicer.app.layoutManager().sliceWidget("Red").mrmlSliceNode()
        if len(state.get("field_of_view", [])) == 3:
            node.SetFieldOfView(*state["field_of_view"])
        if len(state.get("origin", [])) == 3:
            node.SetXYZOrigin(*state["origin"])
        node.UpdateMatrices()

    def flushSave(self):
        self.saveTimer.stop()
        if self.store is None or self.currentRecord is None:
            return True
        try:
            if self.dirty and self.volume is not None:
                background = slicer.app.layoutManager().sliceWidget("Red").mrmlSliceCompositeNode().GetBackgroundVolumeID()
                if background != self.volume.GetID():
                    raise ValueError("当前显示图像不对应所选病例，已停止保存。")
            self.currentRecord = self.store.save(
                self.cases[self.currentIndex]["case_id"], self.currentReader,
                self._zonesFromUI(), self.noteEdit.toPlainText(), self.currentRecord["status"], self._captureDisplay())
            self.dirty = False
            self.setSaveStatus(("已完成 · " if self.currentRecord["status"] == "complete" else "草稿 · ") + "已自动保存")
            self.updateControls()
            return True
        except Exception:
            logging.exception("Pneumoconiosis annotation save failed")
            self.dirty = True
            self.setSaveStatus("保存失败：当前输入仍保留。请重试；暂不能切换或关闭。", error=True)
            return False

    def changeReader(self):
        try:
            reader = validate_reader(self.readerEdit.text)
        except ValueError as exc:
            self.readerEdit.setText(self.currentReader)
            self.showError(str(exc))
            return False
        if reader == self.currentReader:
            return True
        if not self.flushSave():
            self.readerEdit.setText(self.currentReader)
            return False
        self.currentReader = reader
        if self.store and self.currentIndex >= 0:
            self.currentRecord = self.store.get(self.cases[self.currentIndex]["case_id"], reader)
            self.store.set_preference("reader", reader)
            self._populate()
            self.updateControls()
        return True

    def completeCase(self):
        if self.currentRecord is None:
            return False
        try:
            validate_complete(self._zonesFromUI(), self.noteEdit.toPlainText())
        except ValueError as exc:
            self.showError(str(exc))
            return False
        self.currentRecord["status"] = "complete"
        if not self.flushSave():
            self.currentRecord["status"] = "draft"
            return False
        if self.currentIndex + 1 < len(self.cases):
            return self.selectCase(self.currentIndex + 1)
        self.setSaveStatus("最后一例已完成并保存，可以导出结果。")
        return True

    def chooseExport(self):
        if self.store is None or not self.flushSave():
            return
        directory = qt.QFileDialog.getExistingDirectory(slicer.util.mainWindow(), "选择导出目录", str(self.store.directory))
        if directory:
            try:
                paths = self.exportResults(directory)
                self.setSaveStatus("已导出当前读者的 CSV 和 JSON（含未评状态）。")
                qt.QDesktopServices.openUrl(qt.QUrl.fromLocalFile(str(Path(paths[0]).parent)))
            except Exception as exc:
                self.showError("导出失败：" + str(exc))

    def exportResults(self, directory):
        if not self.flushSave():
            raise RuntimeError("保存失败，已停止导出。")
        return self.store.export(directory, [c["case_id"] for c in self.cases], self.currentReader)

    def fitImage(self):
        if self.volume:
            slicer.app.layoutManager().sliceWidget("Red").sliceLogic().FitSliceToAll()

    def resetDisplay(self):
        if self.volume:
            display = self.volume.GetDisplayNode()
            display.AutoWindowLevelOff()
            display.SetWindowLevel(self.defaultDisplay["window"], self.defaultDisplay["level"])
            display.SetAndObserveColorNodeID(self.defaultDisplay["color"])

    def setSaveStatus(self, text, error=False):
        self.saveLabel.setText(text)
        self.saveLabel.setStyleSheet("color:" + ("#e57373" if error else "#5ca989") + "; margin:6px 0;")
        self.retryButton.setVisible(error)

    def showError(self, text):
        self.saveLabel.setText(text)
        self.saveLabel.setStyleSheet("color:#e57373; margin:6px 0;")

    def updateControls(self):
        active = self.currentIndex >= 0 and self.volume is not None
        for control in [self.exportButton, self.caseCombo, self.noteEdit, self.completeButton, self.fitButton, self.resetButton, *self.gradeCombos.values()]:
            control.setEnabled(active)
        self.previousButton.setEnabled(active and self.currentIndex > 0)
        self.nextButton.setEnabled(active and self.currentIndex + 1 < len(self.cases))
        if not active:
            return
        statuses = self.store.completion([c["case_id"] for c in self.cases], self.currentReader)
        count = sum(value == "complete" for value in statuses.values())
        self.progressLabel.setText(f"当前 {self.currentIndex + 1} / {len(self.cases)}　·　已完成 {count} / {len(self.cases)}")
        self.updating = True
        for i, case in enumerate(self.cases):
            status = "已完成" if statuses.get(case["case_id"]) == "complete" else "未完成"
            self.caseCombo.setItemText(i, f"病例 {i + 1:03d} · {case['case_id'][-6:]} · {status}")
        self.updating = False
