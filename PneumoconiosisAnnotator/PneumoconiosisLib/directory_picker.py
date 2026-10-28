"""Directory-only selection using the platform dialog on macOS."""
import sys

import qt


def choose_directory(parent, title, initial, name, *, create=False):
    options = qt.QFileDialog.ShowDirsOnly
    if not create:
        options |= qt.QFileDialog.ReadOnly
    if sys.platform == "darwin":
        # NSOpenPanel avoids the crash in Qt's file-tree accessibility queries.
        # Use the static API so PythonQt's dialog subclass does not force Qt UI.
        return qt.QFileDialog.getExistingDirectory(parent, title, str(initial), options)
    dialog = qt.QFileDialog(parent)
    dialog.setObjectName(name)
    dialog.setOption(qt.QFileDialog.DontUseNativeDialog, True)
    dialog.setOption(qt.QFileDialog.ShowDirsOnly, True)
    dialog.setOption(qt.QFileDialog.ReadOnly, not create)
    dialog.setFileMode(qt.QFileDialog.Directory)
    dialog.setWindowTitle(title)
    dialog.setDirectory(str(initial))
    try:
        return dialog.selectedFiles()[0] if dialog.exec() and dialog.selectedFiles() else ""
    finally:
        dialog.deleteLater()
