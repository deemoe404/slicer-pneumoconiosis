// Read-only regression probe. Arguments: target Slicer PID, output JSON.
import Foundation
import ApplicationServices
let pid = pid_t(CommandLine.arguments[1])!
let output = CommandLine.arguments[2]
let application = AXUIElementCreateApplication(pid)
AXUIElementSetMessagingTimeout(application, 1)
let attributes = ["AXRole", "AXChildren", "AXSelectedChildren", "AXSelectedRows", "AXFocusedUIElement"] as CFArray
var scans = 0
var queries = 0
var successfulQueries = 0
var visited = Set<CFHashCode>()
func scan(_ element: AXUIElement, _ depth: Int) {
    if depth > 14 || visited.count > 1000 { return }
    let hash = CFHash(element)
    if visited.contains(hash) { return }
    visited.insert(hash)
    var values: CFArray?
    let status = AXUIElementCopyMultipleAttributeValues(element, attributes, AXCopyMultipleAttributeOptions(rawValue: 0), &values)
    queries += 1
    if status != .success { return }
    successfulQueries += 1
    if let values = values as? [Any], values.count > 1, let children = values[1] as? [AXUIElement] {
        for child in children { scan(child, depth + 1) }
    }
}
for _ in 0..<240 {
    if kill(pid, 0) != 0 { break }
    visited.removeAll()
    scan(application, 0)
    scans += 1
    let result: [String: Any] = ["trusted": AXIsProcessTrusted(), "scans": scans, "attributeQueries": queries, "successfulQueries": successfulQueries, "targetPid": pid]
    if let data = try? JSONSerialization.data(withJSONObject: result) {
        try? data.write(to: URL(fileURLWithPath: output), options: .atomic)
    }
    usleep(150000)
}
