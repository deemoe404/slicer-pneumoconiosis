"""Compact twelve-step native slider with explicit unread/non-scorable states."""
import qt

from .core import GRADES

GROUP_COLORS = ("#60768c", "#467ca7", "#596fa6", "#7a62a4")
GROUP_BACKGROUNDS = ("#eef3f7", "#eaf3fb", "#edf0fa", "#f2edf9")

class ClickToSetStyle(qt.QProxyStyle):
    """Use Qt's native absolute click and drag handling for the rating sliders."""
    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == qt.QStyle.SH_Slider_AbsoluteSetButtons:
            return qt.Qt.LeftButton
        return self.baseStyle().styleHint(hint, option, widget, returnData)


class GradeRuler(qt.QWidget):
    """One labelled ruler aligned to the native handle centres of all six sliders."""
    def __init__(self, slider, parent=None):
        super().__init__(parent)
        self.slider = slider
        font = self.font
        font.setPixelSize(10)
        self.setFont(font)
        self.setFixedHeight(48)
        self.bands, self.headings = [], []
        for group in range(4):
            band = qt.QFrame(self)
            band.setStyleSheet("background:#f0f3f7; border:1px solid #9eacbd; border-radius:2px;")
            heading = qt.QLabel(str(group), self)
            heading.setAlignment(qt.Qt.AlignCenter)
            heading.setStyleSheet("color:#35475e; font-size:12px; font-weight:600; background:transparent;")
            self.bands.append(band)
            self.headings.append(heading)
        self.labels = []
        for grade in GRADES:
            label = qt.QLabel(grade, self)
            label.setFont(font)
            label.setAlignment(qt.Qt.AlignCenter)
            label.setStyleSheet("color:#394d65; background:#ffffff; border:1px solid #b9c4d1;")
            self.labels.append(label)
        self.timer = qt.QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.connect("timeout()", self.align)
        self.installEventFilter(self)
        self.slider.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() in (qt.QEvent.Resize, qt.QEvent.Move, qt.QEvent.Show, qt.QEvent.StyleChange):
            self.timer.start(0)
        return False

    def align(self):
        origin = self.mapFromGlobal(self.slider.mapToGlobal(qt.QPoint(0, 0))).x()
        step = self.slider.width / len(GRADES)
        for group, (band, heading) in enumerate(zip(self.bands, self.headings)):
            x = origin + round(step * group * 3)
            width = round(step * (group + 1) * 3) - round(step * group * 3) - 2
            band.setGeometry(x, 0, width, 23)
            heading.setGeometry(x, 1, width, 21)
        for index, label in enumerate(self.labels):
            x = origin + round(step * index)
            width = round(step * (index + 1)) - round(step * index)
            label.setGeometry(x, 25, width, 23)


class GradeSlider(qt.QWidget):
    def __init__(self, zone_name, changed, parent=None):
        super().__init__(parent)
        self.changed = changed
        self._value = "unread"
        self.restoring = False
        layout = qt.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.setMinimumHeight(26)
        self.nameLabel = qt.QLabel(zone_name)
        self.nameLabel.setMinimumWidth(28)
        self.nameLabel.setStyleSheet("color:#334155; font-weight:600;")
        layout.addWidget(self.nameLabel)
        self.slider = qt.QSlider(qt.Qt.Horizontal)
        # Keep a separate, slider-owned style; never take ownership of the app's style.
        self.clickStyle = ClickToSetStyle("Fusion")
        self.clickStyle.setParent(self.slider)
        self.slider.setStyle(self.clickStyle)
        self.slider.setLayoutDirection(qt.Qt.LeftToRight)
        self.slider.setMinimumWidth(216)
        self.slider.setFixedHeight(26)
        self._track_width = None
        self.style_track()
        self.slider.setRange(0, len(GRADES) - 1)
        self.slider.setSingleStep(1)
        self.slider.setPageStep(1)
        self.slider.setTickInterval(1)
        self.slider.setTickPosition(qt.QSlider.NoTicks)
        self.slider.setTracking(False)
        self.slider.setAccessibleName(zone_name + "小阴影密集度")
        self.slider.setToolTip("12 档：" + "、".join(GRADES) + "；点击滑轨直接选档，也可拖动或用左右方向键调整。")
        self.slider.installEventFilter(self)
        layout.addWidget(self.slider, 1)
        self.valueLabel = qt.QLabel()
        self.valueLabel.setFixedSize(46, 24)
        self.valueLabel.setAlignment(qt.Qt.AlignCenter)
        layout.addWidget(self.valueLabel)
        self.unreadButton = qt.QToolButton()
        self.unreadButton.setText("清除")
        self.unreadButton.setAutoRaise(True)
        self.unreadButton.setToolTip(zone_name + "：清除评分，恢复未评。")
        self.unreadButton.connect("clicked()", lambda: self.choose_state("unread"))
        layout.addWidget(self.unreadButton)
        self.notScorableButton = qt.QToolButton()
        self.notScorableButton.setText("不可评")
        self.notScorableButton.setCheckable(True)
        self.notScorableButton.setAutoRaise(True)
        self.notScorableButton.setToolTip(zone_name + "：记为不可评价；如需说明，可录入语音备注。")
        for button in (self.unreadButton, self.notScorableButton):
            button.setStyleSheet("""
                QToolButton {color:#68788c; background:#f6f8fb; border:1px solid #ccd4de; border-radius:3px; padding:2px 4px;}
                QToolButton:hover {background:#edf2f7; color:#334155;}
                QToolButton:checked {background:#e8edf4; color:#3e536e;}
                QToolButton:disabled {color:#b0bac7;}
            """)
        self.notScorableButton.connect("clicked()", lambda: self.choose_state("not_scorable"))
        layout.addWidget(self.notScorableButton)
        self.slider.connect("sliderMoved(int)", self.preview)
        self.slider.connect("valueChanged(int)", self.commit)
        self.slider.connect("sliderReleased()", lambda: self.commit(self.slider.sliderPosition))
        self.slider.connect("actionTriggered(int)", self.action)
        self.set_value("unread")

    def style_track(self):
        """Keep twelve visible cells and the native handle on the same grid."""
        width = max(1, self.slider.width)
        if width == self._track_width:
            return
        self._track_width = width
        stops = []
        for cell in range(len(GRADES)):
            start, end = cell / len(GRADES), (cell + 1) / len(GRADES)
            background = "#ffffff" if (cell // 3) % 2 == 0 else "#f0f3f7"
            if cell:
                line = "#778ba4" if cell % 3 == 0 else "#b9c4d1"
                line_end = start + (2 if cell % 3 == 0 else 1) / width
                stops.extend((f"stop:{start:.6f} {line}", f"stop:{line_end:.6f} {line}"))
                start = line_end + .00001
            stops.extend((f"stop:{start:.6f} {background}", f"stop:{end - .00001:.6f} {background}"))
        # A one-cell-wide handle puts each integer value at its cell's centre.
        handle_width = max(12, round(width / len(GRADES)))
        self.slider.setStyleSheet("""
            QSlider::groove:horizontal {height:18px; border:1px solid #aebbc9; border-radius:2px;
                background:qlineargradient(x1:0,y1:0,x2:1,y2:0, %s);}
            QSlider::handle:horizontal {width:%dpx; margin:-2px 0; border-radius:3px;
                background:#dce8f4; border:2px solid #4f7298;}
            QSlider::handle:horizontal:hover {background:#cadff2; border-color:#346795;}
            QSlider::handle:horizontal:pressed {background:#b8d3ed; border-color:#346795;}
            QSlider::groove:horizontal:disabled {background:#e4e8ee;}
            QSlider::handle:horizontal:disabled {border-color:#bac5d2; background:#f7f9fb;}
        """ % (", ".join(stops), handle_width - 4))

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
        self.show_value(self._value)
        self.unreadButton.setEnabled(self._value != "unread")
        self.notScorableButton.setChecked(self._value == "not_scorable")

    def show_value(self, value):
        if value in GRADES:
            group = GRADES.index(value) // 3
            color, background = GROUP_COLORS[group], GROUP_BACKGROUNDS[group]
        else:
            color, background = ("#aa5555", "#fbefef") if value == "unread" else ("#68788c", "#edf1f5")
        self.valueLabel.setText({"unread": "未评", "not_scorable": "不可评"}.get(value, value))
        self.valueLabel.setStyleSheet(f"color:{color}; background:{background}; border:1px solid #ccd4de; border-radius:3px; font-weight:600;")

    def preview(self, position):
        self.show_value(GRADES[position])

    def set_completion_state(self, active, ready):
        self.setProperty("completionState", "ready" if active and ready else "missing" if active else "inactive")
        self.nameLabel.setStyleSheet("font-weight:600; color:" + ("#334155" if active else "#9daaba") + ";")
        self.show_value(self._value)

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
        if obj == self.slider and event.type() == qt.QEvent.Resize:
            self.style_track()
        if obj == self.slider and event.type() == qt.QEvent.Wheel:
            event.ignore()
            return True
        return False
