"""Exercise asynchronous segmentation input blocking and recovery in the real GUI."""
import time

import qt
import slicer


def check_segmentation(w, output, check, events):
    from PneumoconiosisLib import features

    output.mkdir(parents=True, exist_ok=True)
    f, window = w.features, slicer.util.mainWindow()

    def wait():
        deadline = time.monotonic() + 90
        while f.segmenting and time.monotonic() < deadline:
            events(.05)
        check(not f.segmenting and not f.jobs and w.busy.nesting == 0 and w.busy.dialog is None,
              "segmentation releases the entire interface: " + w.saveLabel.text)

    def mouse(widget, point=None):
        point = point or qt.QPoint(widget.width // 2, widget.height // 2)
        for kind, held in ((qt.QEvent.MouseButtonPress, qt.Qt.LeftButton),
                           (qt.QEvent.MouseButtonRelease, qt.Qt.NoButton)):
            event = qt.QMouseEvent(kind, qt.QPointF(point), qt.QPointF(widget.mapToGlobal(point)),
                                  qt.Qt.LeftButton, held, qt.Qt.NoModifier)
            slicer.app.sendEvent(widget, event)

    def exercise_lock():
        dialog = w.busy.dialog
        check(f.segmenting and f.jobs and w.busy.nesting == 1 and dialog is not None and dialog.isVisible()
              and dialog.windowModality == qt.Qt.ApplicationModal and "正在分割" in w.busy.label.text,
              "segmentation immediately displays an application-modal progress overlay")
        check(dialog.size == window.size, "segmentation overlay covers the whole application window")
        grade = f.qualityChoices.value
        zones = w._zonesFromUI()
        reader, case, volume = w.currentReader, w.currentIndex, w.volume.GetID()
        shapes = len(w.spatialState)
        mouse(f.qualityChoices.buttons[3 if grade != 3 else 2])
        slider = w.gradeSliders["RU"].slider
        mouse(slider, qt.QPoint(slider.width - 6, slider.height // 2))
        mouse(w.circleButton)
        mouse(w.nextButton)
        for kind in (qt.QEvent.KeyPress, qt.QEvent.KeyRelease):
            slicer.app.sendEvent(w.readerCombo, qt.QKeyEvent(kind, qt.Qt.Key_Down, qt.Qt.NoModifier))
        check(f.qualityChoices.value == grade and w._zonesFromUI() == zones
              and (w.currentReader, w.currentIndex, w.volume.GetID()) == (reader, case, volume)
              and len(w.spatialState) == shapes and w.markups.placing_id is None,
              "mouse and keyboard input cannot change scores, reader, case or markups during segmentation")
        view = slicer.app.layoutManager().sliceWidget("Red").sliceView()
        node = slicer.app.layoutManager().sliceWidget("Red").mrmlSliceNode()
        fov = node.GetFieldOfView()
        wheel = qt.QWheelEvent(qt.QPointF(30, 30), qt.QPointF(view.mapToGlobal(qt.QPoint(30, 30))),
                              qt.QPoint(0, 0), qt.QPoint(0, 120), qt.Qt.NoButton,
                              qt.Qt.NoModifier, qt.Qt.NoScrollPhase, False)
        slicer.app.sendEvent(view, wheel)
        mime = qt.QMimeData()
        mime.setUrls([qt.QUrl.fromLocalFile(str(w.sourceDirectory))])
        drop = qt.QDropEvent(qt.QPointF(30, 30), qt.Qt.CopyAction, mime, qt.Qt.LeftButton, qt.Qt.NoModifier)
        slicer.app.sendEvent(view, drop)
        check(node.GetFieldOfView() == fov and w.volume.GetID() == volume,
              "the image wheel and directory-drop handlers cannot bypass the segmentation lock")
        check(not w.openDirectory(w.sourceDirectory) and w.volume.GetID() == volume and w.busy.nesting == 1,
              "queued project changes are rejected without releasing the segmentation overlay")
        activated = []
        shortcut = qt.QShortcut(qt.QKeySequence("Ctrl+Alt+J"), window)
        shortcut.connect("activated()", lambda: activated.append(True))
        slicer.app.sendEvent(shortcut, qt.QShortcutEvent(shortcut.key, shortcut.id()))
        check(not activated, "application shortcuts are blocked during segmentation")
        shortcut.setEnabled(False)
        shortcut.deleteLater()
        slicer.app.sendEvent(dialog, qt.QKeyEvent(qt.QEvent.KeyPress, qt.Qt.Key_Escape, qt.Qt.NoModifier))
        dialog.close()
        window.close()
        check(dialog.isVisible() and window.isVisible() and w.busy.nesting == 1,
              "Escape and window-close requests cannot dismiss an active segmentation lock")
        check(dialog.grab().save(str(output / "segmentation-overlay.png")), "segmentation progress overlay captured")

    # Kill a real process while the UI is locked, then exercise its finished signal.
    f.autoButton.click()
    exercise_lock()
    f.model_worker.process.kill()
    wait()
    check("分割进程已停止" in w.saveLabel.text and f.autoButton.isEnabled(),
          "a stopped model process restores input and allows retry")
    original_grade = f.qualityChoices.value
    mouse(f.qualityChoices.buttons[3 if original_grade != 3 else 2])
    check(f.qualityChoices.value != original_grade, "normal clicks work again after segmentation failure")
    f.qualityChoices.set_value(original_grade)
    f.changed()
    w.flushSave()

    worker_type = features.Worker
    def fail_start(*_args, **_kwargs):
        raise RuntimeError("synthetic worker startup failure")
    features.Worker = fail_start
    try:
        check(not f.segment(), "a model startup failure is reported")
        wait()
        check("自动分割未启动" in w.saveLabel.text and f.autoButton.isEnabled(),
              "model startup failure leaves the interface usable")
    finally:
        features.Worker = worker_type

    # A genuine worker error must unwind the same lock without ending the process.
    f.model_worker = worker_type(f.root, "model", f.model_event, f.model_ended)
    send = f.model_worker.send
    f.model_worker.send = lambda job: send(dict(job, input=str(output / "missing-input.npy")))
    try:
        f.autoButton.click()
    finally:
        f.model_worker.send = send
    wait()
    check("自动分割失败" in w.saveLabel.text and f.autoButton.isEnabled()
          and f.model_worker.process.state() == qt.QProcess.Running,
          "an error from a live model worker restores input and permits reuse")

    # Reject a malformed result after real inference; result validation also owns the lock.
    f.model_worker.event = lambda value: f.model_event(dict(value, partition={})
                                                     if value["event"] == "result" else value)
    try:
        f.autoButton.click()
        wait()
        check("分割结果未应用" in w.saveLabel.text and f.partition_state is None and f.autoButton.isEnabled(),
              "result validation failure releases the lock without creating a partition")
    finally:
        f.model_worker.event = f.model_event

    saved_while_locked = []
    flush = w.flushSave
    def checked_save():
        if f.segmenting:
            saved_while_locked.append(w.busy.nesting == 1 and w.busy.dialog is not None)
        return flush()
    w.flushSave = checked_save
    try:
        f.autoButton.click()
        wait()
    finally:
        w.flushSave = flush
    check(saved_while_locked and all(saved_while_locked) and f.partition.state is not None,
          "successful segmentation stays locked through result rendering and persistence")
    check(w.store.get(w.currentRecord["case_id"], w.currentReader)["partition"] == f.partition_state,
          "the interface unlocks only after the segmentation is saved to its original case and reader")
