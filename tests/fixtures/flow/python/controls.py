"""Source fixture only: none of these declarations or decorators may be imported/executed."""
raise RuntimeError('fixture must never execute')


@dangerous_decorator(factory())
async def run(items: 'Sequence', enabled=True) -> 'Result':
    """Only this callable's documentation."""
    value = wrap(first(), second(inner()))
    if check() and allowed():
        await send(value)
    else:
        reject(value)
    for item in load(items):
        if skip(item):
            continue
        consume(item)
        break
        unreachable()
    while pending():
        drain()
    return finish(value)
    unreachable()


def eager():
    """Eager nested comprehension with filtered key/value evaluation."""
    return {key(x): value(y) for x in outer() if keep(x) for y in inner(x) if accept(y)}


def deferred():
    """Declaration defaults and generator creation differ from their deferred bodies."""
    def nested(value=default()):
        forbidden()
    fn = lambda value=seed(): forbidden(value)
    stream = (forbidden(x) for x in source() if hidden(x))
    return use(fn, stream)


def generator():
    """Suspension prevents this body from being modeled as a linear invocation."""
    before()
    yield item()
    after()


def cleanup():
    """Finally can run across returns and exceptions and needs a non-linear model."""
    try:
        body()
        return result()
    finally:
        cleanup_call()
    after()
