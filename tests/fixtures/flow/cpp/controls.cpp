namespace Demo {
class Worker {
public:
    /// Select a result with source-only alternatives.
    int Run(int count) {
        Prepare();
        auto value = flag ? 10 : Fallback();
        if (Ready() && Allowed()) { Accept(Inner()); }
        else { Reject(); }
        for (int i = Seed(); More(i); i++) {
            if (Skip(i)) continue;
            Work(i);
            if (Done()) break;
        }
        for (auto item : Load()) { Use(item); }
        while (Again()) { Tick(); }
        do { Once(); } while (Repeat());
        switch (Code()) {
            case 1: case 2: One(); break;
            default: Other(); break;
        }
        auto deferred = [] { Hidden(); return 4; };
        return Finish(Wrap(value));
    }
    int Run(double count) { return OtherOverload(); }
};
}
