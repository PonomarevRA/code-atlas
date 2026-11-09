/** Class documentation must not become method documentation. */
class Reader {
    /** Read a value using lexical alternatives. */
    suspend fun read(value: String?, ready: Boolean): Int {
        val initial = outer(inner())
        if (check()) {
            yes()
        } else {
            no()
        }
        val selected = value?.trim(argument()) ?: fallback()
        for (item in items()) {
            if (skip(item)) continue
            work(item)
            if (done()) break
        }
        while (again() && more()) {
            tick()
        }
        do {
            once()
        } while (repeat())
        val callback = { deferred() }
        fun nested() { localOnly() }
        launch(context()) { coroutineOnly() }
        value?.let { scopeOnly() }
        val job = async { asyncOnly() }
        job.await()
        return finish(initial)
    }

    fun choose(code: Int): String = when (code) {
        1 -> one()
        2, 3 -> two()
        else -> "other"
    }

    constructor(value: Int) : this(seed(value)) {
        setup(value)
    }

    fun expression(value: Int) = wrap(inner(value))

    fun fail(): Nothing = throw Failure(reason())
}
