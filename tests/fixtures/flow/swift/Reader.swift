/// Type documentation must not be method documentation.
struct Reader {
    /// Read the selected value.
    func run(_ input: Int, name: String) async throws -> String {
        prepare()
        let value = wrap(inner(), other())
        if ready() && permitted() {
            yes(value)
        } else {
            no()
        }
        for item in items() {
            if skip(item) { continue }
            work(item)
            if done() { break }
        }
        while more() { tick() }
        return try await finish(value)
    }
    init(input: Int) { configure(input) }
}
