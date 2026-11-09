/** Type description must not become method documentation. */
class FlowCases {
    /** Evaluate arguments before sending a result. */
    int run(int value, String... names) {
        prepare();
        if (ready() && valid(value)) {
            return send(first(), second(inner()));
        } else {
            throw new Failure(reason());
        }
    }

    FlowCases(int value) {
        this(seed(), value);
        created(value);
    }

    void loops() {
        for (int i = start(); check(i); i = next(i), tick()) {
            if (skip(i)) continue;
            use(i);
            if (done(i)) break;
        }
        for (Item item : load()) { use(item); }
        while (available()) { poll(); }
        do { once(); } while (again());
        after();
    }

    int choose(int value) {
        switch (code()) {
            case 1:
            case 2: one(); break;
            default: other(); break;
        }
        return switch (value) {
            case 1 -> first();
            default -> second();
        };
    }

    void deferred() {
        Runnable task = () -> hiddenLambda();
        Object obj = new Base(argument()) {
            { hiddenInitializer(); }
            void nested() { hiddenMethod(); }
        };
        class Local { void nested() { hiddenLocal(); } }
        visible();
    }

    void cleanup() {
        before();
        try (var resource = open()) { use(resource); }
        catch (Exception error) { recover(); }
        finally { close(); }
        after();
    }
}
