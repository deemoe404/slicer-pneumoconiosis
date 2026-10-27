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
READER_IDS = tuple(f"reader-{number:02d}" for number in range(1, 11))
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))
from PneumoconiosisLib.core import (
    AnnotationStore, GRADES, ZONES, ZONE_NAMES, atomic_write, completion_issues, file_sha256, polygon_issue, scan_cases,
    validate_complete, validate_reader, validate_spatial,
)
from PneumoconiosisLib.markups import ImageMarkups
from PneumoconiosisLib.features import CaseFeatures
from PneumoconiosisLib.core import validate_quality
from PneumoconiosisLib.partition import validate_partition
from PneumoconiosisLib.grade_slider import GradeRuler, GradeSlider
from PneumoconiosisLib.viewer_toolbar import ViewerToolbar


class PneumoconiosisAnnotator(ScriptedLoadableModule):
    def __init__(self, parent):
        super().__init__(parent)
        self.parent.title = "尘肺胸片标注"
        self.parent.categories = ["Research"]
        self.parent.dependencies = ["DICOM", "Volumes", "Colors", "Markups", "Segmentations", "SegmentEditor"]
        self.parent.contributors = ["SUEP CT Research"]
        self.parent.helpText = "整片质量、六区评分、语音录音和医生可修正的自动肺野分区。原图只读，标注自动保存到本地。"
        self.parent.acknowledgementText = "基于 3D Slicer 的科研标注模块。"


class CloseGuard(qt.QWidget):
    def __init__(self, widget):
        super().__init__()
        self.widget = widget

    def eventFilter(self, obj, event):
        if event.type() == qt.QEvent.Close and not self.widget.prepareLeave():
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
        self.projectLock = None
        self.cases = []
        self.currentIndex = -1
        self.currentReader = "reader-01"
        self.currentRecord = None
        self.volume = None
        self.dirty = False
        self.saveError = None
        self.updating = False
        self.entered = False
        self.spatialState = []
        self.spatialUpdating = False
        self.dataRoot = ROOT / "local-data"
        self.saveTimer = qt.QTimer()
        self.saveTimer.setSingleShot(True)
        self.saveTimer.setInterval(450)
        self.saveTimer.connect("timeout()", self.flushSave)

        self.layout.setContentsMargins(12, 8, 12, 8)
        self.headerWidget = qt.QWidget()
        header = qt.QVBoxLayout(self.headerWidget)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(4)
        case_row = qt.QHBoxLayout()
        case_row.setSpacing(4)
        self.caseCombo = qt.QComboBox()
        self.caseCombo.setMinimumContentsLength(16)
        self.caseCombo.setSizeAdjustPolicy(qt.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.caseCombo.setAccessibleName("选择病例")
        self.caseCombo.connect("currentIndexChanged(int)", self.selectCase)
        case_row.addWidget(self.caseCombo, 1)
        self.previousButton = qt.QPushButton("←")
        self.nextButton = qt.QPushButton("→")
        for button, label in ((self.previousButton, "上一例"), (self.nextButton, "下一例")):
            button.setFixedWidth(34)
            button.setMinimumHeight(28)
            button.setToolTip(label)
            button.setAccessibleName(label)
            case_row.addWidget(button)
        self.previousButton.connect("clicked()", lambda: self.selectCase(self.currentIndex - 1))
        self.nextButton.connect("clicked()", lambda: self.selectCase(self.currentIndex + 1))
        self.completeButton = qt.QPushButton("完成并下一例")
        self.completeButton.setMinimumHeight(28)
        self.completeButton.setStyleSheet("font-weight:600;")
        self.completeButton.connect("clicked()", self.completeCase)
        case_row.addWidget(self.completeButton)
        header.addLayout(case_row)

        progress_row = qt.QHBoxLayout()
        progress_row.setSpacing(4)
        self.progressLabel = qt.QLabel("请选择目录")
        self.progressLabel.setStyleSheet("color:#7d8899; font-size:11px;")
        progress_row.addWidget(self.progressLabel, 1)
        progress_row.addWidget(qt.QLabel("读者"))
        self.readerCombo = qt.QComboBox()
        for number, reader in enumerate(READER_IDS, 1):
            self.readerCombo.addItem(f"{number:02d}", reader)
        self.readerCombo.setFixedWidth(58)
        self.readerCombo.setAccessibleName("读者")
        self.readerCombo.setToolTip("每位读者选择自己的编号，评分与标注分别保存。")
        self.readerCombo.connect("currentIndexChanged(int)", self.changeReader)
        progress_row.addWidget(self.readerCombo)
        self.openButton = qt.QPushButton("打开目录")
        self.openButton.setToolTip("选择包含胸片的 DICOM 目录")
        self.openButton.connect("clicked()", self.chooseDirectory)
        progress_row.addWidget(self.openButton)
        self.exportButton = qt.QPushButton("导出结果")
        self.exportButton.connect("clicked()", self.chooseExport)
        progress_row.addWidget(self.exportButton)
        header.addLayout(progress_row)
        self.layout.addWidget(self.headerWidget)

        self.features = CaseFeatures(self, ROOT)
        self.layout.insertWidget(0, self.features.audioWidget)
        self.layout.addWidget(self.features.qualityWidget)

        ratings = qt.QGroupBox("六肺区评分")
        ratings.setToolTip("六肺区小阴影密集度 · GBZ 70—2015")
        self.ratingsWidget = ratings
        ratings_layout = qt.QVBoxLayout(ratings)
        ratings_layout.setSpacing(3)
        self.gradeSliders = {}
        for zone in ZONES:
            control = GradeSlider(ZONE_NAMES[zone], self.ratingChanged, ratings)
            self.gradeSliders[zone] = control
            ratings_layout.addWidget(control)
        self.gradeRuler = GradeRuler(self.gradeSliders[ZONES[0]].slider, ratings)
        ratings_layout.insertWidget(0, self.gradeRuler)
        self.layout.addWidget(ratings)
        self.layout.addWidget(self.features.partitionWidget)

        spatial = qt.QGroupBox("点与区域")
        self.spatialGroup = spatial
        spatial_layout = qt.QVBoxLayout(spatial)
        tools = qt.QHBoxLayout()
        self.pointButton = qt.QPushButton("＋ 标注点")
        self.regionButton = qt.QPushButton("＋ 圈选区域")
        self.finishMarkupButton = qt.QPushButton("结束绘制")
        self.pointButton.connect("clicked()", lambda: self.markups.start("point"))
        self.regionButton.connect("clicked()", lambda: self.markups.start("polygon"))
        self.finishMarkupButton.connect("clicked()", self.finishOrContinueMarkup)
        for button in (self.pointButton, self.regionButton, self.finishMarkupButton):
            tools.addWidget(button)
        spatial_layout.addLayout(tools)
        self.markupList = qt.QListWidget()
        self.markupList.setAccessibleName("点与区域列表")
        self.markupList.setSelectionMode(qt.QAbstractItemView.SingleSelection)
        self.markupList.setUniformItemSizes(True)
        self.markupList.setHorizontalScrollBarPolicy(qt.Qt.ScrollBarAlwaysOff)
        self.markupList.setTextElideMode(qt.Qt.ElideRight)
        self.markupList.connect("currentRowChanged(int)", self.selectMarkup)
        spatial_layout.addWidget(self.markupList)
        selection = qt.QHBoxLayout()
        self.markupNameEdit = qt.QLineEdit()
        self.markupNameEdit.setPlaceholderText("选中标注后可修改名称")
        self.markupNameEdit.setMaxLength(80)
        self.markupNameEdit.connect("textEdited(QString)", self.renameMarkup)
        selection.addWidget(self.markupNameEdit, 1)
        self.deleteMarkupButton = qt.QPushButton("删除")
        self.deleteMarkupButton.connect("clicked()", self.deleteMarkup)
        selection.addWidget(self.deleteMarkupButton)
        spatial_layout.addLayout(selection)
        self.spatialHint = qt.QLabel("单击放点；区域依次点选 ≥3 个顶点，右键结束。\n拖动顶点可调整；按原图像素坐标保存。")
        self.spatialHint.setWordWrap(True)
        self.spatialHint.setStyleSheet("color:#7d8899; font-size:11px;")
        spatial_layout.addWidget(self.spatialHint)
        self.layout.addWidget(spatial)
        self.markups = ImageMarkups(self.spatialChanged, self.updateSpatialControls)

        self.saveLabel = qt.QLabel()
        self.saveLabel.setWordWrap(True)
        self.saveLabel.hide()
        self.layout.addWidget(self.saveLabel)
        self.retryButton = qt.QPushButton("重试保存")
        self.retryButton.connect("clicked()", self.flushSave)
        self.retryButton.hide()
        self.layout.addWidget(self.retryButton)
        self.viewerToolbar = ViewerToolbar(self)

        self.layout.addStretch(1)
        self.closeGuard = CloseGuard(self)
        slicer.util.mainWindow().installEventFilter(self.closeGuard)
        self.wheelGuard = PictureWheelGuard(self)
        slicer.app.installEventFilter(self.wheelGuard)
        self.sceneCloseObserver = slicer.mrmlScene.AddObserver(slicer.vtkMRMLScene.StartCloseEvent, self.onSceneClose)
        self.updateControls()

    def enter(self):
        self.entered = True
        self.viewerToolbar.applyInterface()
        if hasattr(slicer.modules, "DataProbeInstance"):
            annotations = slicer.modules.DataProbeInstance.infoWidget.sliceAnnotations
            annotations.sliceViewAnnotationsEnabled = False
            annotations.updateSliceViewFromGUI()
        red = slicer.app.layoutManager().sliceWidget("Red")
        red.sliceView().setBackgroundColor(qt.QColor("#15191f"))

    def exit(self):
        self.prepareLeave()
        self.entered = False

    def cleanup(self):
        self.saveTimer.stop()
        self.prepareLeave()
        self.viewerToolbar.close()
        self.features.close()
        self.markups.close()
        if getattr(self, "closeGuard", None):
            slicer.util.mainWindow().removeEventFilter(self.closeGuard)
        if getattr(self, "sceneCloseObserver", None):
            slicer.mrmlScene.RemoveObserver(self.sceneCloseObserver)
        if getattr(self, "wheelGuard", None):
            slicer.app.removeEventFilter(self.wheelGuard)
        if self.store:
            self.store.close()
            self.store = None
        if self.projectLock:
            self.projectLock.unlock()
            self.projectLock = None

    def onSceneClose(self, *_):
        saved = self.prepareLeave()
        self.features.clear(preserve=not saved)
        self.markups.clear()
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
        if not self.prepareLeave():
            return False
        source = Path(source).resolve()
        cases, skipped = scan_cases(source)
        if projectDirectory is None:
            key = hashlib.sha256(str(source).encode()).hexdigest()[:16]
            projectDirectory = self.dataRoot / "projects" / key
        projectDirectory = Path(projectDirectory).resolve()
        projectDirectory.mkdir(parents=True, exist_ok=True)
        same_project = self.store is not None and self.store.directory == projectDirectory
        project_lock = self.projectLock if same_project else qt.QLockFile(str(projectDirectory / "reader.lock"))
        project_lock.setStaleLockTime(0)
        if not same_project and not project_lock.tryLock(100):
            raise ValueError("这个标注项目正由另一个窗口使用，请先关闭那个窗口。")
        new_store = AnnotationStore(projectDirectory)
        try:
            new_store.import_cases(cases)
            recovered = new_store.recover_audio()
            new_reader = validate_reader(reader or new_store.preference("reader", "reader-01"))
            if new_reader not in READER_IDS:
                raise ValueError("读者请选择 01–10。")
        except Exception:
            new_store.close()
            if not same_project:
                project_lock.unlock()
            raise
        if self.store:
            self.store.close()
        if self.projectLock and not same_project:
            self.projectLock.unlock()
        self.projectLock = project_lock
        self.features.clear()
        self.markups.clear()
        self.spatialState = []
        self.store, self.cases = new_store, cases
        self.currentReader = new_reader
        self.currentIndex, self.currentRecord = -1, None
        self.dirty = False
        self.updating = True
        self.readerCombo.setCurrentIndex(self.readerCombo.findData(new_reader))
        self.caseCombo.clear()
        for index, case in enumerate(cases):
            self.caseCombo.addItem(f"病例 {index + 1:03d} · {case['case_id'][-6:]}")
        self.updating = False
        last_id = new_store.preference("last_case:" + new_reader)
        index = next((i for i, case in enumerate(cases) if case["case_id"] == last_id), 0)
        opened = self.selectCase(index)
        self.progressLabel.setToolTip(f"扫描时跳过 {skipped} 个非目标或重复文件。" if skipped else "")
        if opened:
            self.dataRoot.mkdir(parents=True, exist_ok=True)
            atomic_write(self.dataRoot / "last-project.json", json.dumps({"source": str(source), "project": str(new_store.directory)}, ensure_ascii=False))
            if recovered:
                self.setSaveStatus(f"已恢复 {recovered} 段未关联的本地录音。")
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
        if not self.prepareLeave():
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
        self._restoreSpatial()
        if old_volume:
            sh = slicer.vtkMRMLSubjectHierarchyNode.GetSubjectHierarchyNode(slicer.mrmlScene)
            item = sh.GetItemByDataNode(old_volume)
            if item:
                sh.RemoveItem(item)
            else:
                slicer.mrmlScene.RemoveNode(old_volume)
        self._populate()
        self.features.restore()
        self.store.set_preference("last_case:" + self.currentReader, self.cases[index]["case_id"])
        self.updateControls()
        return True

    def _resetCombo(self):
        self.updating = True
        self.caseCombo.setCurrentIndex(self.currentIndex)
        self.updating = False

    def _populate(self):
        self.updating = True
        for zone, control in self.gradeSliders.items():
            value = self.currentRecord["zones"][zone]
            token = value["grade"] if value["state"] == "scored" else value["state"]
            control.set_value(token)
        self.caseCombo.setCurrentIndex(self.currentIndex)
        self.updating = False
        self.dirty = False
        self.setSaveStatus("已完成 · 已保存" if self.currentRecord["status"] == "complete" else ("草稿已恢复" if self.currentRecord["updated_at"] else "未评 · 修改后自动保存"))

    def _zonesFromUI(self):
        values = {}
        for zone, control in self.gradeSliders.items():
            token = control.value
            values[zone] = {"state": "scored" if token in GRADES else token, "grade": token if token in GRADES else None}
        return values

    def ratingChanged(self, *_):
        if self.updating or self.currentRecord is None:
            return
        self.currentRecord["status"] = "draft"
        self.dirty = True
        self.flushSave()

    def _restoreSpatial(self):
        self.spatialState = self.currentRecord["spatial_annotations"]
        self.markups.restore(self.volume, self.spatialState)
        self.spatialState = self.markups.capture()
        self.updateSpatialControls()

    def spatialChanged(self, annotations):
        self.spatialState = annotations
        if self.updating or self.currentRecord is None:
            return
        self.currentRecord["status"] = "draft"
        self.dirty = True
        self.setSaveStatus("正在保存…")
        self.saveTimer.start()
        self.updateSpatialControls()

    def updateSpatialControls(self):
        if not hasattr(self, "markups"):
            return
        self.spatialUpdating = True
        try:
            previous = self.markupList.currentItem()
            previous_id = previous.data(qt.Qt.UserRole) if previous else None
            scroll = self.markupList.verticalScrollBar().value
            identifiers = list(self.markups.nodes)
            if [self.markupList.item(row).data(qt.Qt.UserRole) for row in range(self.markupList.count)] != identifiers:
                self.markupList.clear()
                for identifier in identifiers:
                    item = qt.QListWidgetItem("", self.markupList)
                    item.setData(qt.Qt.UserRole, identifier)
            selected_item = None
            for row, (identifier, node) in enumerate(self.markups.nodes.items()):
                kind = node.GetAttribute("PneumoconiosisKind")
                count = node.GetNumberOfDefinedControlPoints()
                record = next((v for v in self.spatialState if v["id"] == identifier), None)
                draft = kind == "polygon" and (record is None or polygon_issue(record["points_ijk"]))
                suffix = " · 草稿" if draft else (" · 待放置" if count == 0 else "")
                text = ("点 · " if kind == "point" else "区域 · ") + node.GetName() + suffix
                item = self.markupList.item(row)
                item.setText(text)
                item.setToolTip(text)
                if identifier == self.markups.selected_id:
                    selected_item = item
            self.markupList.setCurrentRow(self.markupList.row(selected_item) if selected_item is not None else -1)
            row_height = max(qt.QFontMetrics(self.markupList.font).height() + 8, self.markupList.sizeHintForRow(0))
            rows = min(5, max(1, self.markupList.count))
            self.markupList.setFixedHeight(rows * row_height + 2 * self.markupList.frameWidth)
            if selected_item is not None and previous_id != self.markups.selected_id:
                self.markupList.scrollToItem(selected_item)
            else:
                self.markupList.verticalScrollBar().setValue(scroll)
            selected = self.markups.nodes.get(self.markups.selected_id)
            placing = bool(self.markups.placing_id)
            active = self.volume is not None and self.currentRecord is not None
            self.pointButton.setEnabled(active)
            self.regionButton.setEnabled(active)
            self.markupList.setEnabled(active and bool(self.markups.nodes))
            self.deleteMarkupButton.setEnabled(active and selected is not None)
            self.markupNameEdit.setEnabled(active and selected is not None)
            if not self.markupNameEdit.hasFocus():
                self.markupNameEdit.setText(selected.GetName() if selected else "")
            self.finishMarkupButton.setText("结束绘制" if placing else "继续绘制")
            self.finishMarkupButton.setEnabled(active and (placing or (selected is not None and
                (selected.GetAttribute("PneumoconiosisKind") == "polygon" or selected.GetNumberOfDefinedControlPoints() == 0))))
            if placing:
                self.spatialHint.setText("正在绘制：在胸片上单击放点，右键或点“结束绘制”完成。")
            else:
                self.spatialHint.setText("单击放点；区域依次点选 ≥3 个顶点，右键结束。\n拖动顶点可调整；按原图像素坐标保存。")
        finally:
            self.spatialUpdating = False
        self.updateCompletionControls()

    def selectMarkup(self, row):
        if not self.spatialUpdating:
            item = self.markupList.item(row)
            self.markups.select(item.data(qt.Qt.UserRole) if item else None)

    def renameMarkup(self, text):
        if not self.spatialUpdating and text.strip():
            self.markups.rename(text.strip())

    def deleteMarkup(self):
        self.markups.remove()
        self.flushSave()

    def finishOrContinueMarkup(self):
        if self.markups.placing_id:
            self.markups.finish()
            if self.flushSave():
                record = next((r for r in self.currentRecord["spatial_annotations"] if r["id"] == self.markups.selected_id), None)
                if record and record["state"] == "draft":
                    self.showError(record["label"] + "：" + polygon_issue(record["points_ijk"]) + "已保存草稿，可继续绘制。")
        else:
            self.markups.continue_placing()
        self.updateSpatialControls()

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
                self._zonesFromUI(), self.currentRecord["status"], self._captureDisplay(),
                spatial_annotations=self.spatialState, **self.features.capture())
            self.dirty = False
            self.setSaveStatus(("已完成 · " if self.currentRecord["status"] == "complete" else "草稿 · ") + "已自动保存")
            self.updateControls()
            return True
        except Exception as exc:
            logging.exception("Pneumoconiosis annotation save failed")
            self.dirty = True
            self.setSaveStatus("保存失败：" + str(exc) + " 当前输入仍保留，暂不能切换或关闭。", error=True)
            return False

    def prepareLeave(self):
        if not self.features.prepare_leave():
            return False
        self.markups.finish()
        return self.flushSave()

    def changeReader(self, *_):
        if self.updating:
            return True
        reader = self.readerCombo.currentData
        if reader == self.currentReader:
            return True
        if not self.prepareLeave():
            self.readerCombo.blockSignals(True)
            self.readerCombo.setCurrentIndex(self.readerCombo.findData(self.currentReader))
            self.readerCombo.blockSignals(False)
            return False
        self.currentReader = reader
        if self.store and self.currentIndex >= 0:
            self.currentRecord = self.store.get(self.cases[self.currentIndex]["case_id"], reader)
            self._restoreSpatial()
            self.store.set_preference("reader", reader)
            self._populate()
            self.features.restore()
            self.updateControls()
        return True

    def completeCase(self):
        if self.currentRecord is None:
            return False
        issues = self.updateCompletionControls()
        # Saving/recording can be retried by this action; form errors need correction first.
        form_issues = [message for area, message in issues.items() if area not in ("save", "audio")]
        if form_issues:
            self.showError("\n".join(form_issues))
            return False
        try:
            validate_complete(self._zonesFromUI())
            validate_spatial(self.spatialState, self.cases[self.currentIndex], complete=True)
            values = self.features.capture()
            validate_quality(values["quality"], complete=True)
            validate_partition(values["partition"], self.cases[self.currentIndex], self.store.assets, complete=True)
        except ValueError as exc:
            self.showError(str(exc))
            return False
        if not self.features.prepare_leave():
            return False
        self.markups.finish()
        self.currentRecord["status"] = "complete"
        if not self.flushSave():
            self.currentRecord["status"] = "draft"
            return False
        if self.currentIndex + 1 < len(self.cases):
            return self.selectCase(self.currentIndex + 1)
        self.setSaveStatus("最后一例已完成并保存，可以导出结果。")
        return True

    def chooseExport(self):
        if self.store is None or not self.prepareLeave():
            return
        directory = qt.QFileDialog.getExistingDirectory(slicer.util.mainWindow(), "选择导出目录", str(self.store.directory))
        if directory:
            try:
                paths = self.exportResults(directory)
                self.setSaveStatus("已导出评分、质量、坐标、录音与分区文件。")
                qt.QDesktopServices.openUrl(qt.QUrl.fromLocalFile(str(Path(paths[0]).parent)))
            except Exception as exc:
                self.showError("导出失败：" + str(exc))

    def exportResults(self, directory):
        if not self.prepareLeave():
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
        if error:
            self.saveError = text
        elif not self.dirty:
            self.saveError = None
        self.saveLabel.setText(self.saveError or text)
        self.saveLabel.setStyleSheet("color:#e57373; margin:6px 0;")
        self.saveLabel.setVisible(bool(self.saveError))
        self.retryButton.setVisible(bool(self.saveError))
        self.updateCompletionControls()

    def showError(self, text):
        self.saveLabel.setText(text)
        self.saveLabel.setStyleSheet("color:#e57373; margin:6px 0;")
        self.saveLabel.show()

    def updateCompletionControls(self):
        active = self.currentRecord is not None and self.volume is not None
        issues = {}
        partition = self.features.partition.state or self.features.partition_state
        zones = self._zonesFromUI()
        if active:
            quality = {**self.currentRecord["quality"], "grade": self.features.qualityChoices.value}
            issues = completion_issues(zones, quality,
                                       self.spatialState, partition, self.cases[self.currentIndex])
            if self.features.audio_stop_error:
                issues["audio"] = self.features.audio_stop_error
            if self.saveError:
                issues["save"] = self.saveError
        for key, widget, title, optional in (
            ("quality", self.features.qualityWidget, "整片质量", False),
            ("ratings", self.ratingsWidget, "六肺区评分", False),
            ("partition", self.features.partitionWidget, "肺野与六区", partition is None),
            ("spatial", self.spatialGroup, "点与区域", not self.spatialState),
            ("audio", self.features.audioWidget, "语音备注", True),
        ):
            issue = issues.get(key)
            state = "missing" if issue else "ready" if active else "inactive"
            if widget.property("completionState") != state:
                widget.setProperty("completionState", state)
                name = "completion_" + key
                widget.setObjectName(name)
                color = "#b43e3e" if issue else "#207544"
                widget.setStyleSheet(
                    f"QGroupBox#{name} {{border:1px solid {color}; border-radius:4px; margin-top:1.1em;}}"
                    f"QGroupBox#{name}::title {{subcontrol-origin:margin; left:6px; padding:0 3px; color:{color};}}"
                    if active else "")
            suffix = (" · 待完成" if issue else " · 可选 ✓" if optional else " ✓") if active else ""
            widget.setTitle(title + suffix)
            widget.setToolTip(issue or ("此项可不填写。" if optional else "已满足完成条件。") if active else "请选择胸片目录。")
            widget.setAccessibleDescription(widget.toolTip)
        for zone, control in self.gradeSliders.items():
            ready = zones[zone]["state"] != "unread"
            control.set_completion_state(active, ready)
        state = "missing" if issues else "ready" if active else "inactive"
        if self.completeButton.property("completionState") != state:
            self.completeButton.setProperty("completionState", state)
            color, background = ("#b43e3e", "#fff1f1") if issues else ("#207544", "#edf8f0")
            self.completeButton.setStyleSheet(
                f"QPushButton {{color:{color}; background-color:{background}; border:1px solid {color};"
                "border-radius:4px; padding:4px 7px; font-weight:600;}" if active else "font-weight:600;")
        hint = "\n".join(issues.values()) if issues else "已满足完成条件，点击后保存并进入下一例。"
        self.completeButton.setToolTip(hint if active else "请选择胸片目录。")
        self.completeButton.setAccessibleDescription(self.completeButton.toolTip)
        return issues

    def updateControls(self):
        active = self.currentIndex >= 0 and self.volume is not None
        for control in [self.exportButton, self.caseCombo, self.readerCombo, self.completeButton, self.ratingsWidget]:
            control.setEnabled(active)
        self.viewerToolbar.updateControls()
        self.previousButton.setEnabled(active and self.currentIndex > 0)
        self.nextButton.setEnabled(active and self.currentIndex + 1 < len(self.cases))
        self.updateSpatialControls()
        self.features.update_controls()
        if not active:
            return
        statuses = self.store.completion([c["case_id"] for c in self.cases], self.currentReader)
        count = sum(value == "complete" for value in statuses.values())
        self.progressLabel.setText(f"{self.currentIndex + 1}/{len(self.cases)} · 完成 {count}")
        self.updating = True
        for i, case in enumerate(self.cases):
            status = "已完成" if statuses.get(case["case_id"]) == "complete" else "未完成"
            self.caseCombo.setItemText(i, f"病例 {i + 1:03d} · {case['case_id'][-6:]} · {status}")
        self.updating = False
