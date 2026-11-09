<?php
/** Контекст Reader. */
class Reader {
    /** Возвращает выбранное значение.
     * @param ?Service $service источник
     * @return ?string результат
     */
    public function run(?Service $service, int $count = 2): ?string {
        Before();
        if (Check()) {
            Yes();
        } elseif (Other()) {
            No();
        } else {
            Last();
        }
        $value = First() ?? Fallback();
        $service?->load(Argument());
        for (Init(); Test(); Update()) {
            if (Skip()) continue;
            Work();
            if (Done()) break;
        }
        foreach (Load() as $key => $item) {
            UseItem($item);
        }
        $closure = fn($x) => Deferred($x);
        return Wrap(Inner());
    }

    public function Wrap($x) { return $x; }
}
