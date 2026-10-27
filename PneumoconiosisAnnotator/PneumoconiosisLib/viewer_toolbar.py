"""Image controls using Slicer's native interactions, independent of the module panel."""
import qt
import slicer
import vtk


class ViewerToolbar:
    def __init__(self, widget):
        self.w = widget
        self.fullInterface = False
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
        self.transformButton = self._button(row, "缩放 / 平移", "滚轮缩放；按住鼠标中键拖动平移。")
        self.transformButton.setCheckable(True)
        self.transformButton.connect("clicked()", lambda: self.setMouseMode(slicer.vtkMRMLInteractionNode.ViewTransform))
        self.fitButton = self._button(row, "适应窗口", "显示完整胸片。")
        self.fitButton.connect("clicked()", self.w.fitImage)
        self.resetButton = self._button(row, "原始灰度", "恢复读取这张胸片时的窗宽、窗位与灰度映射。")
        self.resetButton.connect("clicked()", self.w.resetDisplay)
        row.addStretch(1)
        self.fullButton = self._button(row, "完整 Slicer 界面", "展开原生工具栏、模块选择器和视图控制；可再次点击返回。")
        self.fullButton.connect("clicked()", lambda: self.setFullInterface(not self.fullInterface))
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
        if self.w.volume is None:
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
        for button in (self.windowLevelButton, self.transformButton, self.fitButton, self.resetButton):
            button.setEnabled(active)
        self.updateMouseMode()

    def _showNativeControls(self, visible):
        slicer.util.setToolbarsVisible(visible)
        slicer.util.setDataProbeVisible(visible)
        slicer.util.setModulePanelTitleVisible(visible)
        slicer.util.setModuleHelpSectionVisible(visible)
        manager = slicer.app.layoutManager()
        for name in manager.sliceViewNames():
            manager.sliceWidget(name).sliceController().setVisible(visible)

    def applyInterface(self):
        self._showNativeControls(self.fullInterface)
        if not self.fullInterface:
            slicer.app.layoutManager().setLayout(slicer.vtkMRMLLayoutNode.SlicerLayoutOneUpRedSliceView)
            slicer.app.layoutManager().sliceWidget("Red").sliceController().hide()
            if self.w.volume is not None:
                slicer.util.setSliceViewerLayers(background=self.w.volume)
        self.fullButton.setText("精简标注界面" if self.fullInterface else "完整 Slicer 界面")
        self.fullButton.setAccessibleName(self.fullButton.text)
        self.frame.show()

    def setFullInterface(self, full):
        self.fullInterface = bool(full)
        if not full:
            slicer.util.selectModule("PneumoconiosisAnnotator")
        self.applyInterface()

    def close(self):
        self.interaction.RemoveObserver(self.observer)
        self._showNativeControls(True)
        self.stack.removeWidget(self.viewport)
        self.stack.removeWidget(self.frame)
        self.rootLayout.removeItem(self.stack)
        self.rootLayout.insertWidget(self.layoutIndex, self.viewport)
        self.frame.hide()
        self.frame.deleteLater()
        self.stack.deleteLater()
