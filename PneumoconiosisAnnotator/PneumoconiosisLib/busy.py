"""One application-modal overlay for operations that temporarily prevent editing."""
from contextlib import contextmanager
from functools import wraps

import qt
import slicer


class BusyOverlay(qt.QWidget):
    def __init__(self):
        super().__init__()
        self.dialog = None
        self.nesting = 0

    def begin(self, text, cancel=None):
        self.nesting += 1
        if self.dialog is not None:
            return
        window = slicer.util.mainWindow()
        self.dialog = qt.QDialog(window, qt.Qt.Dialog | qt.Qt.FramelessWindowHint)
        self.dialog.setObjectName("PneumoBusyOverlay")
        self.dialog.setWindowModality(qt.Qt.ApplicationModal)
        self.dialog.setAttribute(qt.Qt.WA_TranslucentBackground)
        self.dialog.setStyleSheet("QFrame#PneumoBusyShade {background:rgba(15,22,30,155);}")
        self.dialog.installEventFilter(self)
        outer = qt.QVBoxLayout(self.dialog)
        outer.setContentsMargins(0, 0, 0, 0)
        shade = qt.QFrame()
        shade.setObjectName("PneumoBusyShade")
        outer.addWidget(shade)
        layout = qt.QVBoxLayout(shade)
        layout.addStretch(1)
        row = qt.QHBoxLayout()
        row.addStretch(1)
        panel = qt.QFrame()
        panel.setObjectName("PneumoBusyCard")
        panel.setStyleSheet("QFrame#PneumoBusyCard {background:#fff; border-radius:10px;} QLabel {color:#20262e; background:transparent;}")
        panel.setMinimumWidth(340)
        card = qt.QVBoxLayout(panel)
        card.setContentsMargins(28, 24, 28, 24)
        label = qt.QLabel(text)
        label.setObjectName("PneumoBusyText")
        label.setAlignment(qt.Qt.AlignCenter)
        label.setStyleSheet("font-size:18px; font-weight:600;")
        card.addWidget(label)
        self.label = label
        note = qt.QLabel("请稍候，完成后将自动恢复操作。")
        note.setAlignment(qt.Qt.AlignCenter)
        card.addWidget(note)
        progress = qt.QProgressBar()
        progress.setRange(0, 0)
        progress.setTextVisible(False)
        progress.setFixedHeight(5)
        card.addWidget(progress)
        self.progress = progress
        if cancel is not None:
            button = qt.QPushButton("取消导出")
            button.connect("clicked()", cancel)
            card.addWidget(button)
        row.addWidget(panel)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(1)
        self.dialog.setGeometry(qt.QRect(window.mapToGlobal(qt.QPoint(0, 0)), window.size))
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.repaint()
        slicer.app.processEvents(qt.QEventLoop.ExcludeUserInputEvents)

    def set_progress(self, current, total):
        if self.dialog is not None:
            self.label.setText(f"正在导出图像与标注… {current}/{total}")
            self.progress.setRange(0, total)
            self.progress.setValue(current)

    def end(self):
        self.nesting = max(0, self.nesting - 1)
        if self.nesting or self.dialog is None:
            return
        dialog, self.dialog = self.dialog, None
        dialog.removeEventFilter(self)
        dialog.hide()
        dialog.deleteLater()

    def eventFilter(self, obj, event):
        return bool(obj == self.dialog and (event.type() == qt.QEvent.Close or
                    (event.type() == qt.QEvent.KeyPress and event.key() == qt.Qt.Key_Escape)))

    @contextmanager
    def during(self, text):
        self.begin(text)
        try:
            yield
        finally:
            self.end()


def blocking(text):
    def decorate(function):
        @wraps(function)
        def run(self, *args, **kwargs):
            widget = getattr(self, "w", self)
            with widget.busy.during(text):
                return function(self, *args, **kwargs)
        return run
    return decorate
