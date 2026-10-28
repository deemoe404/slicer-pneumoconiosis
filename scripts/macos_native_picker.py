"""Drive the real NSOpenPanel in Slicer GUI checks; no production picker bypass."""
import ctypes as c
import os
import subprocess
import tempfile
import time
from pathlib import Path

import qt
import slicer

objc = c.CDLL("/usr/lib/libobjc.A.dylib")
objc.objc_getClass.argtypes, objc.objc_getClass.restype = [c.c_char_p], c.c_void_p
objc.sel_registerName.argtypes, objc.sel_registerName.restype = [c.c_char_p], c.c_void_p
driver_directory = None


def native_driver():
    global driver_directory
    if driver_directory is None:
        driver_directory = tempfile.TemporaryDirectory(prefix="pneumo-native-picker-")
        root = Path(driver_directory.name)
        subprocess.run(["/usr/bin/swiftc", "-module-cache-path", str(root / "cache"),
                        str(Path(__file__).with_suffix(".swift")), "-o", str(root / "driver")],
                       check=True, capture_output=True)
    return str(Path(driver_directory.name) / "driver")


def send(target, selector, *arguments, result=c.c_void_p, types=()):
    # AppKit actions can synchronously reenter PythonQt event filters. Keep the
    # GIL while calling Objective-C, as PythonQt expects on the GUI thread.
    function = c.PYFUNCTYPE(result, c.c_void_p, c.c_void_p, *types)(("objc_msgSend", objc))
    return function(target, objc.sel_registerName(selector.encode()), *arguments)


def native_panels():
    app = send(objc.objc_getClass(b"NSApplication"), "sharedApplication")
    windows = send(app, "windows")
    panel_class = objc.objc_getClass(b"NSOpenPanel")
    for i in range(send(windows, "count", result=c.c_ulong)):
        window = send(windows, "objectAtIndex:", i, types=(c.c_ulong,))
        if send(window, "isKindOfClass:", panel_class, result=c.c_bool, types=(c.c_void_p,)) and send(window, "isVisible", result=c.c_bool):
            yield window


def drive_native_picker(action, directory=None, *, hold_seconds=.8):
    visited, errors, drivers = [], [], []
    executable = native_driver()
    started = time.monotonic()
    def select():
        if not visited and time.monotonic() - started > 12:
            errors.append("Expected NSOpenPanel, but the native panel did not appear")
            timer.stop()
            for dialog in slicer.util.findChildren(slicer.util.mainWindow(), className="QFileDialog"):
                if dialog.isVisible():
                    dialog.reject()
        for panel in native_panels():
            try:
                if not visited:
                    assert send(panel, "canChooseDirectories", result=c.c_bool)
                    assert not send(panel, "canChooseFiles", result=c.c_bool)
                    visited.append(time.monotonic())
                if time.monotonic() - visited[0] < hold_seconds:
                    return
                if directory is None:
                    send(panel, "cancel:", None, types=(c.c_void_p,))
                    return
                if not drivers:
                    drivers.append(subprocess.Popen([executable, str(os.getpid()), str(directory)],
                                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE))
                if drivers and drivers[0].poll() not in (None, 0):
                    raise AssertionError(drivers[0].communicate()[1].decode(errors="replace"))
                if time.monotonic() - started > 12:
                    raise AssertionError("Native directory selection timed out")
            except Exception as exc:
                errors.append(str(exc))
                timer.stop()
                send(panel, "cancel:", None, types=(c.c_void_p,))
    timer = qt.QTimer()
    timer.connect("timeout()", select)
    timer.start(50)
    try:
        result = action()
    finally:
        timer.stop()
    for driver in drivers:
        _, error = driver.communicate(timeout=3)
        if driver.returncode:
            errors.append(error.decode(errors="replace"))
    assert visited and not errors, errors or "No real NSOpenPanel was shown"
    return result
