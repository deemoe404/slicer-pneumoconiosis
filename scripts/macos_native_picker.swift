// GUI test driver: operate only the specified Slicer process's native open panel.
import AppKit
import ApplicationServices

let pid = pid_t(CommandLine.arguments[1])!
let directory = CommandLine.arguments[2]
let application = AXUIElementCreateApplication(pid)
func attribute(_ element: AXUIElement, _ name: String) -> CFTypeRef? {
    var value: CFTypeRef?
    return AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success ? value : nil
}
func key(_ code: CGKeyCode, _ flags: CGEventFlags = []) {
    guard NSWorkspace.shared.frontmostApplication?.processIdentifier == pid else {
        fputs("The test app lost focus; refusing to send a key\n", stderr)
        exit(3)
    }
    for down in [true, false] {
        let event = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: down)!
        event.flags = flags
        event.post(tap: .cghidEventTap)
    }
}
guard AXIsProcessTrusted() else { fputs("Accessibility permission is required for the GUI test\n", stderr); exit(1) }
NSRunningApplication(processIdentifier: pid)?.activate(options: [.activateIgnoringOtherApps])
usleep(150000)
key(5, [.maskCommand, .maskShift]) // Go to Folder
var field: AXUIElement?
for _ in 0..<40 {
    usleep(50000)
    if let value = attribute(application, "AXFocusedUIElement"), CFGetTypeID(value) == AXUIElementGetTypeID() {
        let element = value as! AXUIElement
        let role = attribute(element, "AXRole") as? String
        if role == "AXTextField" || role == "AXComboBox" {
            field = element
            break
        }
    }
}
guard let field = field,
      AXUIElementSetAttributeValue(field, "AXValue" as CFString, directory as CFString) == .success else {
    fputs("Cannot enter the test folder in the native Go to Folder field\n", stderr)
    key(53)
    exit(2)
}
key(36) // Navigate to the entered folder.
usleep(800000)
key(36) // Choose that folder.
