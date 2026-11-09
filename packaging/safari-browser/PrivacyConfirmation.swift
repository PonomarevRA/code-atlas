import Foundation
import LocalAuthentication

/// Lock-protected callback state; authentication never exposes a password to this executable.
final class AuthenticationResult: @unchecked Sendable {
    private let lock = NSLock()
    private var result: Bool?
    func finish(_ value: Bool) { lock.lock(); defer { lock.unlock() }; if result == nil { result = value } }
    func snapshot() -> Bool? { lock.lock(); defer { lock.unlock() }; return result }
}

let context = LAContext()
context.touchIDAuthenticationAllowableReuseDuration = 0
var error: NSError?
let available = context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &error)
if CommandLine.arguments == [CommandLine.arguments[0], "--probe"] {
    print(available ? "available" : "unavailable")
    exit(0)
}
guard CommandLine.arguments.count == 1, available else { exit(1) }
let state = AuthenticationResult()
context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: "Разрешить передачу оригинальных данных агентам при ослаблении защиты") { success, _ in state.finish(success) }
let deadline = Date(timeIntervalSinceNow: 20)
while state.snapshot() == nil && Date() < deadline { RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.05)) }
context.invalidate()
exit(state.snapshot() == true ? 0 : 1)
