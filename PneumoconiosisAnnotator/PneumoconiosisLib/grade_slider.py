"""Compact twelve-step native slider with explicit unread/non-scorable states."""
import qt

from .core import GRADES


class GradeRuler(qt.QWidget):
    """One labelled ruler aligned to the native handle centres of all six sliders."""
    def __init__(self, slider, parent=None):
        super().__init__(parent)
        self.slider = slider
        font = self.font
        font.setPixelSize(10)
        self.setFont(font)
        self.setFixedHeight(qt.QFontMetrics(font).height() + 4)
        self.labels = []
        for grade in GRADES:
            label = qt.QLabel(grade, self)
            label.setFont(font)
            label.setAlignment(qt.Qt.AlignCenter)
            label.setStyleSheet("color:#657181;")
            self.labels.append(label)
        self.timer = qt.QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.connect("timeout()", self.align)
        self.installEventFilter(self)
        self.slider.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() in (qt.QEvent.Resize, qt.QEvent.Move, qt.QEvent.Show):
            self.timer.start(0)
        return False

    def align(self):
        origin = self.mapFromGlobal(self.slider.mapToGlobal(qt.QPoint(0, 0))).x()
        handle = self.slider.style().pixelMetric(qt.QStyle.PM_SliderLength)
        span = max(0, self.slider.width - handle)
        metrics = qt.QFontMetrics(self.font)
        for index, label in enumerate(self.labels):
            width = metrics.horizontalAdvance(GRADES[index]) + 2
            centre = origin + handle / 2 + span * index / (len(GRADES) - 1)
            label.setGeometry(round(centre - width / 2), 0, width, self.height)


class GradeSlider(qt.QWidget):
    def __init__(self, zone_name, changed, parent=None):
        super().__init__(parent)
        self.changed = changed
        self._value = "unread"
        self.completionColor = None
        self.restoring = False
        layout = qt.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.nameLabel = qt.QLabel(zone_name)
        self.nameLabel.setMinimumWidth(28)
        layout.addWidget(self.nameLabel)
        self.slider = qt.QSlider(qt.Qt.Horizontal)
        self.slider.setLayoutDirection(qt.Qt.LeftToRight)
        self.slider.setMinimumWidth(190)
        self.slider.setRange(0, len(GRADES) - 1)
        self.slider.setSingleStep(1)
        self.slider.setPageStep(1)
        self.slider.setTickInterval(1)
        self.slider.setTickPosition(qt.QSlider.TicksBelow)
        self.slider.setTracking(False)
        self.slider.setAccessibleName(zone_name + "小阴影密集度")
        self.slider.setToolTip("12 档：" + "、".join(GRADES) + "；拖动或用左右方向键调整。")
        self.slider.installEventFilter(self)
        layout.addWidget(self.slider, 1)
        self.valueLabel = qt.QLabel()
        self.valueLabel.setMinimumWidth(40)
        self.valueLabel.setAlignment(qt.Qt.AlignCenter)
        layout.addWidget(self.valueLabel)
        self.unreadButton = qt.QToolButton()
        self.unreadButton.setText("未评")
        self.unreadButton.setCheckable(True)
        self.unreadButton.setAutoRaise(True)
        self.unreadButton.setToolTip(zone_name + "：清除评分，恢复未评。")
        self.unreadButton.connect("clicked()", lambda: self.choose_state("unread"))
        layout.addWidget(self.unreadButton)
        self.notScorableButton = qt.QToolButton()
        self.notScorableButton.setText("不可评")
        self.notScorableButton.setCheckable(True)
        self.notScorableButton.setAutoRaise(True)
        self.notScorableButton.setToolTip(zone_name + "：不可评价，完成时需注明原因。")
        self.notScorableButton.connect("clicked()", lambda: self.choose_state("not_scorable"))
        layout.addWidget(self.notScorableButton)
        self.slider.connect("sliderMoved(int)", self.preview)
        self.slider.connect("valueChanged(int)", self.commit)
        self.slider.connect("sliderReleased()", lambda: self.commit(self.slider.sliderPosition))
        self.slider.connect("actionTriggered(int)", self.action)
        self.set_value("unread")

    @property
    def value(self):
        return self._value

    def set_value(self, value):
        self.restoring = True
        try:
            self._value = value
            self.slider.setValue(GRADES.index(value) if value in GRADES else 0)
            self.refresh()
        finally:
            self.restoring = False

    def refresh(self):
        self.valueLabel.setText({"unread": "未评", "not_scorable": "不可评"}.get(self._value, self._value))
        self.valueLabel.setStyleSheet("font-weight:600;" + ("color:" + self.completionColor + ";" if self.completionColor else ""))
        self.unreadButton.setChecked(self._value == "unread")
        self.notScorableButton.setChecked(self._value == "not_scorable")

    def preview(self, position):
        self.valueLabel.setText(GRADES[position])
        self.valueLabel.setStyleSheet("font-weight:600;")

    def set_completion_state(self, active, ready):
        self.setProperty("completionState", "ready" if active and ready else "missing" if active else "inactive")
        self.completionColor = ("#207544" if ready else "#b43e3e") if active else None
        style = "color:" + self.completionColor + "; font-weight:600;" if active else ""
        self.nameLabel.setStyleSheet(style)
        self.valueLabel.setStyleSheet(style)

    def commit(self, position):
        if self.restoring:
            return
        value = GRADES[position]
        previous, self._value = self._value, value
        self.refresh()
        if value != previous:
            self.changed()

    def action(self, _action):
        if not self.slider.isSliderDown():
            self.commit(self.slider.sliderPosition)

    def choose_state(self, value):
        previous = self._value
        self.set_value(value)
        if value != previous:
            self.changed()

    def eventFilter(self, obj, event):
        if obj == self.slider and event.type() == qt.QEvent.Wheel:
            event.ignore()
            return True
        return False
