struct Cleanup {
    /// Cleanup remains deferred across guard exits.
    func run() async throws {
        defer { cleanup() }
        guard let value = read() else { throw makeError() }
        let callback = { hidden() }
        use(value)
        await flush()
    }
}
