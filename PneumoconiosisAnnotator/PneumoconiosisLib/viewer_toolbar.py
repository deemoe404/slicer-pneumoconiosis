"""Image controls using Slicer's native interactions, independent of the module panel."""
import qt
import slicer
import vtk

from .image_view import set_background


class ViewerToolbar:
    def __init__(self, widget):
        self.w = widget
        self.fullInterface = False
        self.observedVolume = None
        self.displayObserver = None
        self.interaction = slicer.app.applicationLogic().GetInteractionNode()
        central = slicer.util.mainWindow().centralWidget()
        self.viewport = slicer.app.layoutManager().viewport()
        self.rootLayout = central.layout()
        self.layoutIndex = self.rootLayout.indexOf(self.viewport)
        self.stack = qt.QVBoxLayout()
        self.stack.setContentsMargins(0, 0, 0, 0)
        self.stack.setSpacing(0)
        self.rootLayout.removeWidget(self.viewport)
        self.rootLayout.insertLayout(self.layoutIndex, self.stack)

        self.frame = qt.QFrame(central)
        self.frame.setObjectName("PneumoViewerToolbar")
        self.frame.setStyleSheet("""
            QFrame#PneumoViewerToolbar {background:#20262e; border-bottom:1px solid #39424d;}
            QFrame#PneumoViewerToolbar QToolButton {
                color:#edf1f5; background:transparent; border:1px solid transparent;
                border-radius:4px; padding:5px 8px;
            }
            QFrame#PneumoViewerToolbar QToolButton:hover {background:#34404d;}
            QFrame#PneumoViewerToolbar QToolButton:checked {background:#345773; border-color:#6898bd;}
            QFrame#PneumoViewerToolbar QToolButton:disabled {color:#7d8899;}
        """)
        row = qt.QHBoxLayout(self.frame)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(4)

        self.windowLevelButton = self._button(row, "窗宽窗位", "在胸片上按住左键拖动，调整明暗和对比度。")
        self.windowLevelButton.setCheckable(True)
        self.windowLevelButton.connect("clicked()", lambda: self.setMouseMode(slicer.vtkMRMLInteractionNode.AdjustWindowLevel))
        self.windowLevelLabel = qt.QLabel("窗宽 —  窗位 —")
        self.windowLevelLabel.setMinimumWidth(160)
        self.windowLevelLabel.setStyleSheet("color:#c2ceda; padding:0 4px;")
        self.windowLevelLabel.setAccessibleName("当前窗宽和窗位")
        self.windowLevelLabel.setToolTip("实时读取当前胸片的显示参数，仅影响显示。")
        row.addWidget(self.windowLevelLabel)
        self.transformButton = self._button(row, "缩放 / 平移", "滚轮缩放；按住鼠标中键拖动平移。")
        self.transformButton.setCheckable(True)
        self.transformButton.connect("clicked()", lambda: self.setMouseMode(slicer.vtkMRMLInteractionNode.ViewTransform))
        self.fitButton = self._button(row, "适应窗口", "显示完整胸片。")
        self.fitButton.connect("clicked()", self.w.fitImage)
        self.resetButton = self._button(row, "原始灰度", "恢复读取这张胸片时的窗宽、窗位与灰度映射。")
        self.resetButton.connect("clicked()", self.w.resetDisplay)
        self.referenceFolderButton = self._button(row, "标准片", "从外部文件夹加载标准片，在当前胸片右侧对照。")
        self.referenceFolderButton.connect("clicked()", lambda: self.w.references.choose_directory())
        self.referenceButton = self._button(row, "对照", "显示或收起已经加载的标准片。")
        self.referenceButton.setCheckable(True)
        self.referenceButton.setEnabled(False)
        self.referenceButton.connect("clicked(bool)", lambda checked: self.w.references.set_visible(checked))
        row.addStretch(1)
        self.fullButton = self._button(row, "完整 Slicer 界面", "展开原生工具栏、模块选择器和视图控制；可再次点击返回。")
        self.fullButton.connect("clicked()", self.toggleInterface)
        self.moduleSelector = slicer.util.moduleSelector()
        self.moduleSelector.connect("moduleSelected(QString)", self.updateInterfaceButton)
        self.stack.addWidget(self.frame)
        self.stack.addWidget(self.viewport, 1)
        self.observer = self.interaction.AddObserver(vtk.vtkCommand.ModifiedEvent, self.updateMouseMode)
        self.updateControls()

    @staticmethod
    def _button(row, text, hint):
        button = qt.QToolButton()
        button.setText(text)
        button.setToolTip(hint)
        button.setAccessibleName(text)
        row.addWidget(button)
        return button

    def setMouseMode(self, mode):
        if self.w.volume is None and self.w.references.volume is None:
            return
        self.w.markups.finish()
        self.w.features.partition.close_editor()
        self.w.features.editPointsButton.setChecked(False)
        self.interaction.SetCurrentInteractionMode(mode)
        self.updateMouseMode()

    def updateMouseMode(self, *_):
        mode = self.interaction.GetCurrentInteractionMode()
        self.windowLevelButton.setChecked(mode == self.interaction.AdjustWindowLevel)
        self.transformButton.setChecked(mode == self.interaction.ViewTransform)

    def updateControls(self):
        active = self.w.volume is not None and self.w.currentRecord is not None
        references = getattr(self.w, "references", None)
        for button in (self.windowLevelButton, self.transformButton):
            button.setEnabled(active or (references is not None and references.volume is not None))
        for button in (self.fitButton, self.resetButton):
            button.setEnabled(active)
        if self.observedVolume is not self.w.volume:
            self._stopObservingDisplay()
            self.observedVolume = self.w.volume
            if self.observedVolume is not None:
                self.displayObserver = self.observedVolume.AddObserver(
                    slicer.vtkMRMLDisplayableNode.DisplayModifiedEvent, self.updateWindowLevel)
        self.updateWindowLevel()
        self.updateMouseMode()

    def updateWindowLevel(self, *_):
        display = self.observedVolume.GetDisplayNode() if self.observedVolume is not None else None
        text = f"窗宽 {display.GetWindow():.1f}  窗位 {display.GetLevel():.1f}" if display else "窗宽 —  窗位 —"
        if self.windowLevelLabel.text != text:
            self.windowLevelLabel.setText(text)

    def _stopObservingDisplay(self):
        if self.observedVolume is not None and self.displayObserver is not None:
            self.observedVolume.RemoveObserver(self.displayObserver)
        self.observedVolume = None
        self.displayObserver = None

    def _showNativeControls(self, visible):
        slicer.util.setToolbarsVisible(visible)
        window = slicer.util.mainWindow()
        window.menuBar().setVisible(visible)
        # These native panels are optional and can be absent during startup.
        for probe in slicer.util.findChildren(window, name="DataProbeCollapsibleWidget"):
            probe.setVisible(visible)
        if slicer.util.findChildren(window, name="PanelDockWidget"):
            slicer.util.setModulePanelTitleVisible(visible)
        if slicer.util.findChildren(window, name="ModulePanel"):
            slicer.util.setModuleHelpSectionVisible(visible)
        manager = slicer.app.layoutManager()
        for name in manager.sliceViewNames():
            view = manager.sliceWidget(name)
            if view is not None and view.sliceController() is not None:
                view.sliceController().setVisible(visible)

    def applyInterface(self):
        references = getattr(self.w, "references", None)
        if not self.fullInterface:
            if references is not None:
                references.apply_layout()
            else:
                slicer.app.layoutManager().setLayout(slicer.vtkMRMLLayoutNode.SlicerLayoutOneUpRedSliceView)
            if self.w.volume is not None:
                set_background(self.w.volume)
        self._showNativeControls(self.fullInterface)
        self.updateInterfaceButton()
        self.frame.show()

    def updateInterfaceButton(self, *_):
        returning = self.fullInterface or slicer.util.selectedModule() != "PneumoconiosisAnnotator"
        self.fullButton.setText("返回标注界面" if returning else "完整 Slicer 界面")
        self.fullButton.setAccessibleName(self.fullButton.text)
        self.fullButton.setToolTip("返回病例、质量评价和六肺区评分。" if returning else "展开原生工具栏、模块选择器和视图控制。")

    def toggleInterface(self):
        returning = self.fullInterface or slicer.util.selectedModule() != "PneumoconiosisAnnotator"
        self.setFullInterface(not returning)

    def setFullInterface(self, full):
        self.fullInterface = bool(full)
        if not full:
            slicer.util.selectModule("PneumoconiosisAnnotator")
        self.applyInterface()

    def close(self):
        self.moduleSelector.disconnect("moduleSelected(QString)", self.updateInterfaceButton)
        self._stopObservingDisplay()
        self.interaction.RemoveObserver(self.observer)
        self._showNativeControls(True)
        self.stack.removeWidget(self.viewport)
        self.stack.removeWidget(self.frame)
        self.rootLayout.removeItem(self.stack)
        self.rootLayout.insertWidget(self.layoutIndex, self.viewport)
        self.frame.hide()
        self.frame.deleteLater()
        self.stack.deleteLater()
