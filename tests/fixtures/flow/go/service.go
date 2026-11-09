package sample

type Service struct{}

// Run preserves the selected declaration's own documentation.
func (s *Service) Run(items []int, ready bool) int {
    before := Wrap(Seed())
    if guard := Ready(); guard && Check() {
        Yes(before)
    } else if Other() {
        No()
    }
    for i := Start(); Test(i); i = Next(i) {
        if Skip(i) { continue }
        Work(i)
        if Stop(i) { break }
    }
    for _, item := range Load() {
        Use(item)
    }
    switch Tag() {
    case First(), Second():
        One()
    case Third():
        Two()
    default:
        OtherCase()
    }
    switch value := Input().(type) {
    case int, string:
        Typed(value)
    default:
        Unknown(value)
    }
    return Wrap(Finish())
}

// Deferred separates immediate argument evaluation from later invocations.
func (s *Service) Deferred() {
    defer Clean(Arg())
    go Worker(SpawnArg())
    callback := func() { NeverInline() }
    UseCallback(callback)
}

// Concurrent demonstrates a scheduling boundary that must remain opaque.
func (s *Service) Concurrent(ch chan int) {
    select {
    case value := <-ch:
        Receive(value)
    case ch <- MakeValue():
        Sent()
    default:
        Fallback()
    }
}
