"""Check Chinese native widgets and DICOM decoding using fictional metadata only."""
from pathlib import Path
import json
import shutil

import ctk
import pydicom
from pydicom.uid import generate_uid
import qt
import slicer
from DICOMLib import DICOMUtils


def check_language(w, output, check, events):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    check(slicer.app.translate("qSlicerMainWindow", "&File").replace("&", "") == "文件",
          "native Slicer menu translations are loaded from the offline package")
    dialog = qt.QFileDialog(slicer.util.mainWindow())
    dialog.setOption(qt.QFileDialog.DontUseNativeDialog, True)
    dialog.setFileMode(qt.QFileDialog.Directory)
    buttons = slicer.util.findChildren(dialog, className="QPushButton")
    check(any("取消" in button.text for button in buttons), "native directory picker buttons are Chinese")
    dialog.deleteLater()

    source = Path(w.cases[w.currentIndex]["file_path"])
    original_project = w.store.directory
    dropped = output / "拖入 胸片"
    dropped.mkdir(exist_ok=True)
    shutil.copy2(source, dropped / "合成胸片.dcm")
    mime = qt.QMimeData()
    mime.setUrls([qt.QUrl.fromLocalFile(str(dropped))])
    window = slicer.util.mainWindow()
    enter = qt.QDragEnterEvent(qt.QPoint(200, 200), qt.Qt.CopyAction, mime, qt.Qt.LeftButton, qt.Qt.NoModifier)
    slicer.app.sendEvent(window, enter)
    drop = qt.QDropEvent(qt.QPointF(200, 200), qt.Qt.CopyAction, mime, qt.Qt.LeftButton, qt.Qt.NoModifier)
    slicer.app.sendEvent(window, drop)
    events(.5)
    check(enter.isAccepted() and drop.isAccepted() and w.headerWidget.isVisible()
          and slicer.util.selectedModule() == "PneumoconiosisAnnotator" and w.currentRecord is None
          and w.sourceDirectory == dropped.resolve() and w.store is None,
          "dropping a chest folder selects its source and waits for an explicit project")
    check(w.openDirectory(dropped, original_project, create=False), "the matching project reopens after a folder drop")
    check(not window.menuBar().isVisible(), "the doctor interface hides the general-purpose Slicer menus")

    # Selecting a native module reproduces the missing annotation panel without
    # correcting selection in the test before checking the visible UI.
    slicer.util.selectModule("DICOM")
    events(.2)
    dicom = slicer.modules.dicom.widgetRepresentation().self()
    check("导入" in dicom.ui.importButton.text and "数据库" in dicom.ui.showBrowserButton.text,
          "native DICOM import and database buttons are Chinese")
    check(w.viewerToolbar.fullButton.text == "返回标注界面", "native module selection exposes a clear return action")
    slicer.util.mainWindow().grab().save(str(output / "native-chinese.png"))
    w.viewerToolbar.fullButton.click()
    events(.1)
    check(slicer.util.selectedModule() == "PneumoconiosisAnnotator" and w.headerWidget.isVisible()
          and not w.viewerToolbar.fullInterface, "one click restores the annotation panel from the native DICOM module")

    values = {"0010,0010": ("PatientName", "合成^测试"),
              "0008,1030": ("StudyDescription", "合成胸片检查"),
              "0008,103E": ("SeriesDescription", "中文显示验证")}
    results = {}
    with DICOMUtils.TemporaryDICOMDatabase(str(output / "dicom-cache")) as database:
        for charset in ("ISO_IR 192", "GB18030"):
            ds = pydicom.dcmread(source)
            ds.SpecificCharacterSet = charset
            ds.SOPInstanceUID = ds.file_meta.MediaStorageSOPInstanceUID = generate_uid()
            ds.StudyInstanceUID, ds.SeriesInstanceUID = generate_uid(), generate_uid()
            ds.PatientID = "SYNTHETIC-" + charset.replace(" ", "-")
            for field, value in values.values():
                setattr(ds, field, value)
            path = output / (charset.replace(" ", "-") + ".dcm")
            ds.save_as(path, enforce_file_format=True)
            indexer = ctk.ctkDICOMIndexer()
            indexer.addFile(database, str(path), False)
            indexer.waitForImportFinished()
            actual = {field: database.fileValue(str(path), tag) for tag, (field, _) in values.items()}
            check(all(actual[field] == value for field, value in values.values()),
                  charset + " DICOM patient, study and series text survives the actual native reader")
            results[charset] = actual
    return results
