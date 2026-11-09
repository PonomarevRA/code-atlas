//! Module comments belong to the module.
trait Worker {
    /// Describes a trait contract only.
    fn pending(&self, value: i32) -> i32;
}
struct Engine;
impl Engine {
    /// Loads input and selects a result.
    #[inline]
    async fn run(&self, input: Option<i32>) -> Result<i32, E> {
        prepare();
        let Some(value) = load(input)? else { return fallback(); };
        if let Some(number) = find(value) {
            accept(number);
        } else {
            reject();
        }
        match classify() {
            1 | 2 => first(inner()),
            _ => second(),
        };
        for item in items() {
            if skip(item) { continue; }
            process(item);
            if done(item) { break; }
        }
        while let Some(item) = next() { consume(item); }
        let later = |x| deferred(x);
        let future = async { deferred_async().await };
        fn local() { deferred_local(); }
        opaque!(not_a_call());
        return finish(argument()).await;
    }
}

fn prepare() {}
