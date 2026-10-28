"""An external reference library in a separate, unlinked native Slicer view."""
import json
import logging
from pathlib import Path

from .busy import blocking

import qt
import slicer

from .core import atomic_write
from .image_view import orient_image, remove_volume, set_background
from .references import read_reference, scan_references

VIEW_NAME = "PneumoReference"
LAYOUT_ID = 1621
LAYOUT = """
<layout type="horizontal" split="true">
  <item><view class="vtkMRMLSliceNode" singletontag="Red">
    <property name="orientation" action="default">Axial</property>
    <property name="viewlabel" action="default">R</property>
  </view></item>
  <item><view class="vtkMRMLSliceNode" singletontag="PneumoReference">
    <property name="orientation" action="default">Axial</property>
    <property name="viewlabel" action="default">Ref</property>
    <property name="viewcolor" action="default">#6d92b5</property>
  </view></item>
</layout>
"""


class ReferenceView(qt.QWidget):
    def __init__(self, widget, toolbar):
        super().__init__(slicer.util.mainWindow())
        self.w, self.toolbar = widget, toolbar
        self.root = None
        self.images = []
        self.index = -1
        self.volume = None
        self.visible = False
        self.restored = False
        self.displayObserver = None
        self.defaults = None
        self.bar = qt.QFrame()
        self.bar.setObjectName("PneumoReferenceBar")
        self.bar.setStyleSheet("""
            QFrame#PneumoReferenceBar {background:#20262e; border-bottom:1px solid #39424d;}
            QFrame#PneumoReferenceBar QLabel {color:#c2ceda;}
            QFrame#PneumoReferenceBar QToolButton {
                color:#edf1f5; background:transparent; border:1px solid transparent;
                border-radius:4px; padding:4px 6px;
            }
            QFrame#PneumoReferenceBar QToolButton:hover {background:#34404d;}
            QFrame#PneumoReferenceBar QToolButton:disabled {color:#7d8899;}
        """)
        stack = qt.QVBoxLayout(self.bar)
        stack.setContentsMargins(8, 4, 8, 4)
        row = qt.QHBoxLayout()
        row.setSpacing(4)
        row.addWidget(qt.QLabel("标准片（右侧）"))
        self.combo = qt.QComboBox()
        self.combo.setMinimumContentsLength(8)
        self.combo.setSizeAdjustPolicy(qt.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.combo.setAccessibleName("选择标准片")
        self.combo.connect("currentIndexChanged(int)", self.choose_image)
        row.addWidget(self.combo, 1)
        self.previousButton = toolbar._button(row, "←", "上一张标准片")
        self.nextButton = toolbar._button(row, "→", "下一张标准片")
        self.previousButton.connect("clicked()", lambda: self.choose_image(self.index - 1))
        self.nextButton.connect("clicked()", lambda: self.choose_image(self.index + 1))
        self.windowLevelLabel = qt.QLabel()
        self.windowLevelLabel.setToolTip("右侧标准片的显示参数；左、右两张图的显示调整相互独立。")
        row.addWidget(self.windowLevelLabel)
        self.fitButton = toolbar._button(row, "适应窗口", "适应右侧标准片，不改变当前胸片。")
        self.fitButton.connect("clicked()", self.fit)
        self.resetButton = toolbar._button(row, "原始灰度", "恢复标准片加载时的灰度显示。")
        self.resetButton.connect("clicked()", self.reset_display)
        self.folderButton = toolbar._button(row, "换目录", "选择外部标准片文件夹。")
        self.folderButton.connect("clicked()", self.choose_directory)
        self.hideButton = toolbar._button(row, "×", "收起标准片，返回单幅胸片显示。")
        self.hideButton.connect("clicked()", lambda: self.set_visible(False))
        stack.addLayout(row)
        self.errorLabel = qt.QLabel()
        self.errorLabel.setWordWrap(True)
        self.errorLabel.setStyleSheet("color:#f1bb67;")
        self.errorLabel.hide()
        stack.addWidget(self.errorLabel)
        toolbar.stack.insertWidget(1, self.bar)
        self.bar.hide()
        slicer.app.installEventFilter(self)

    @property
    def settings_path(self):
        return self.w.dataRoot / "reference-library.json"

    def choose_directory(self):
        dialog = qt.QFileDialog(slicer.util.mainWindow())
        dialog.setObjectName("PneumoReferenceDirectoryDialog")
        dialog.setOption(qt.QFileDialog.DontUseNativeDialog, True)
        dialog.setOption(qt.QFileDialog.ShowDirsOnly, True)
        dialog.setOption(qt.QFileDialog.ReadOnly, True)
        dialog.setFileMode(qt.QFileDialog.Directory)
        dialog.setWindowTitle("选择标准片文件夹")
        initial = str(self.root) if self.root and self.root.is_dir() else qt.QStandardPaths.writableLocation(qt.QStandardPaths.DocumentsLocation)
        dialog.setDirectory(initial)
        try:
            if dialog.exec() and dialog.selectedFiles():
                self.open_directory(dialog.selectedFiles()[0])
        except Exception as exc:
            logging.exception("Cannot open the reference library")
            self.show_error("标准片打开失败：" + str(exc))
        finally:
            dialog.deleteLater()

    @blocking("正在打开标准片目录…")
    def open_directory(self, directory, selected=None, visible=True):
        root = Path(directory).expanduser().resolve()
        images = scan_references(root)
        index = next((i for i, image in enumerate(images) if image["relative_path"] == selected), 0)
        # Decode before replacing a working reference or its remembered folder.
        volume, defaults = self._load(images[index])
        self._remove_volume()
        self.root, self.images, self.index = root, images, index
        self.volume, self.defaults = volume, defaults
        self._observe_display()
        self.combo.blockSignals(True)
        self.combo.clear()
        for image in images:
            self.combo.addItem(image["label"])
        self.combo.setCurrentIndex(index)
        self.combo.blockSignals(False)
        self.combo.setToolTip(f"{root}\n共 {len(images)} 张；按文件名显示，不自动推断评分。")
        self.show_error("")
        self.set_visible(visible)
        return True

    def _load(self, image):
        data = read_reference(image["path"])
        volume = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLScalarVolumeNode", "标准片 · " + image["label"])
        try:
            volume.SetAttribute("PneumoconiosisReference", "1")
            volume.SetHideFromEditors(True)
            volume.SetSaveWithScene(False)
            volume.SetSpacing(*data["spacing"])
            slicer.util.updateVolumeFromArray(volume, data["pixels"][None])
            volume.CreateDefaultDisplayNodes()
            display = volume.GetDisplayNode()
            display.SetSaveWithScene(False)
            display.AutoWindowLevelOff()
            display.SetWindowLevel(data["window"], data["level"])
            color = "vtkMRMLColorTableNodeInvertedGrey" if data["inverted"] else "vtkMRMLColorTableNodeGrey"
            display.SetAndObserveColorNodeID(color)
            return volume, {"window": data["window"], "level": data["level"], "color": color}
        except Exception:
            remove_volume(volume)
            raise

    @blocking("正在切换标准片…")
    def choose_image(self, index):
        if index < 0 or index >= len(self.images) or index == self.index:
            return
        try:
            volume, defaults = self._load(self.images[index])
        except Exception as exc:
            self.combo.blockSignals(True)
            self.combo.setCurrentIndex(self.index)
            self.combo.blockSignals(False)
            self.show_error("这张标准片无法读取：" + str(exc))
            return
        self._remove_volume()
        self.volume, self.defaults, self.index = volume, defaults, index
        self._observe_display()
        self.combo.blockSignals(True)
        self.combo.setCurrentIndex(index)
        self.combo.blockSignals(False)
        self.show_error("")
        self.bind_view(orient=True)
        self.update_controls()
        self.save_settings()

    def apply_layout(self):
        manager = slicer.app.layoutManager()
        node = manager.layoutLogic().GetLayoutNode()
        if not node.IsLayoutDescription(LAYOUT_ID):
            node.AddLayoutDescription(LAYOUT_ID, LAYOUT)
        layout = LAYOUT_ID if self.visible else slicer.vtkMRMLLayoutNode.SlicerLayoutOneUpRedSliceView
        if manager.layout != layout:
            manager.setLayout(layout)
        red = manager.sliceWidget("Red")
        red.mrmlSliceNode().SetViewGroup(1)
        if self.visible:
            view = manager.sliceWidget(VIEW_NAME)
            view.mrmlSliceNode().SetViewGroup(2)
            view.mrmlSliceCompositeNode().SetDoPropagateVolumeSelection(False)
            view.sliceView().setBackgroundColor(qt.QColor("#15191f"))
            self.bind_view()

    def bind_view(self, orient=False):
        view = slicer.app.layoutManager().sliceWidget(VIEW_NAME)
        if view is not None and self.volume is not None:
            set_background(self.volume, VIEW_NAME)
            if orient:
                orient_image(self.volume, VIEW_NAME)

    def set_visible(self, visible):
        self.visible = bool(visible and self.volume is not None)
        if not self.visible:
            self.show_error("")
        self.toolbar.referenceButton.setChecked(self.visible)
        self.apply_layout()
        self.toolbar.applyInterface()
        if self.visible:
            self.bind_view(orient=True)
            # The splitter's final size is assigned after this event returns.
            qt.QTimer.singleShot(0, self.fit)
        self.w.fitImage()
        self.update_controls()
        self.save_settings()

    def fit(self):
        view = slicer.app.layoutManager().sliceWidget(VIEW_NAME)
        if view is not None and self.volume is not None:
            view.sliceLogic().FitSliceToAll()

    def reset_display(self):
        if self.volume is not None:
            display = self.volume.GetDisplayNode()
            display.AutoWindowLevelOff()
            display.SetWindowLevel(self.defaults["window"], self.defaults["level"])
            display.SetAndObserveColorNodeID(self.defaults["color"])

    def _observe_display(self):
        self.displayObserver = self.volume.AddObserver(slicer.vtkMRMLDisplayableNode.DisplayModifiedEvent, self.update_controls)

    def update_controls(self, *_):
        active = self.volume is not None
        self.previousButton.setEnabled(active and self.index > 0)
        self.nextButton.setEnabled(active and self.index + 1 < len(self.images))
        for control in (self.combo, self.fitButton, self.resetButton):
            control.setEnabled(active)
        self.toolbar.referenceButton.setEnabled(active)
        display = self.volume.GetDisplayNode() if active else None
        self.windowLevelLabel.setText(f"窗宽 {display.GetWindow():.0f}  窗位 {display.GetLevel():.0f}" if display else "")
        self.bar.setVisible(self.visible or bool(self.errorLabel.text))
        self.toolbar.updateControls()

    def show_error(self, text):
        self.errorLabel.setText(text)
        self.errorLabel.setVisible(bool(text))
        self.update_controls()

    def save_settings(self):
        if self.root is None or not self.images or self.index < 0:
            return
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(self.settings_path, json.dumps({"directory": str(self.root),
                "selected": self.images[self.index]["relative_path"], "visible": self.visible}, ensure_ascii=False))
        except OSError:
            logging.exception("Cannot remember the reference directory")
            self.show_error("无法记住标准片目录；下次启动时请重新选择。")

    def restore_once(self):
        if self.restored:
            return
        self.restored = True
        if not self.settings_path.is_file():
            return
        try:
            saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
            self.open_directory(saved["directory"], saved.get("selected"), bool(saved.get("visible", True)))
        except Exception as exc:
            logging.warning("Cannot restore external reference library", exc_info=True)
            self.show_error("上次的标准片目录无法读取，请点击「标准片」重新选择。" + str(exc))

    def eventFilter(self, obj, event):
        # A placement click in the reference must never become an annotation on the case.
        if self.visible and event.type() in (qt.QEvent.MouseButtonPress, qt.QEvent.MouseButtonRelease, qt.QEvent.MouseButtonDblClick):
            interaction = slicer.app.applicationLogic().GetInteractionNode()
            view = slicer.app.layoutManager().sliceWidget(VIEW_NAME).sliceView()
            if (interaction.GetCurrentInteractionMode() == interaction.Place and event.button() == qt.Qt.LeftButton
                    and obj.isWidgetType() and (obj == view or view.isAncestorOf(obj))):
                return True
        return False

    def _remove_volume(self):
        if self.volume is not None:
            if self.displayObserver is not None:
                self.volume.RemoveObserver(self.displayObserver)
            remove_volume(self.volume)
        self.volume = None
        self.displayObserver = None

    def clear(self):
        self._remove_volume()
        self.visible = False
        self.images, self.index = [], -1
        self.combo.clear()
        self.toolbar.referenceButton.setChecked(False)
        self.update_controls()

    def close(self):
        slicer.app.removeEventFilter(self)
        self.clear()
        self.toolbar.stack.removeWidget(self.bar)
        self.bar.hide()
        self.bar.deleteLater()
        self.deleteLater()
