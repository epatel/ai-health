@file:JvmName("Orders")

package demo.orders

import androidx.compose.foundation.layout.Column
import kotlinx.coroutines.launch

typealias Listener = (Order) -> Unit

/** Orders, a repository and a screen: modern Kotlin and Compose syntax. */
sealed interface Shape {
    data class Circle(val r: Double) : Shape
    data object Empty : Shape
}

enum class Status(val label: String) {
    OPEN("o") {
        override fun next() = CLOSED
    },
    CLOSED("c") {
        override fun next() = OPEN
    };

    abstract fun next(): Status
}

@JvmInline
value class OrderId(val raw: Long)

interface Priced {
    val price: Double
    fun discounted(rate: Double): Double = price * (1 - rate)
}

class OrderRepository(private val api: Api, var count: Int = 0) : Base(), Api by api {
    private val cache = mutableMapOf<String, Int>()
    val size: Int
        get() = cache.size
    var name: String = ""
        set(value) {
            field = value.trim()
        }

    init {
        require(this.count >= 0) // never negative
    }

    constructor(api: Api) : this(api, 1)

    companion object {
        const val MAX = 3
        fun create(): OrderRepository = OrderRepository(Api())
    }

    fun find(id: String?, vararg tags: String, strict: Boolean = false): Int {
        val v = id?.length ?: return -1
        outer@ for (i in 0 until v) {
            for (t in tags) {
                if (t.isEmpty()) continue@outer else break@outer
            }
        }
        while (count > 0 && strict || !strict) {
            count--
        }
        return when (v) {
            0, 1 -> 0
            in 2..5 -> 1
            else -> try { v / count } catch (e: ArithmeticException) { 0 }
        }
    }

    fun classify(s: Shape): String = when (s) {
        is Shape.Circle if s.r > 1.0 -> "big"
        is Shape.Circle -> "small"
        Shape.Empty -> "empty"
    }

    suspend fun load() = coroutineScope {
        launch { api.call()?.let { cache[it] = 1 } }
        cache.forEach { (k, v) -> if (v > 0) println("$k=$v") }
    }

    tailrec fun fact(n: Int, acc: Long = 1): Long = if (n <= 1) acc else fact(n - 1, acc * n)
}

fun String.shout(): String = uppercase().also { println(it) }!!

val onOrder: Listener = { order -> println(order) }

val ticker = object : Runnable {
    override fun run() {
        println("tick")
    }
}

context(logger: Logger)
fun logged(message: String) = logger.info(message)

fun template() = $$"price: $$amount, not $interpolated"

@Composable
fun OrderScreen(state: UiState, modifier: Modifier = Modifier, onClick: () -> Unit = {}) {
    Column(modifier = modifier.padding(16.dp)) {
        if (state.loading) {
            CircularProgressIndicator()
        } else {
            LazyColumn {
                items(state.rows) { row ->
                    Row {
                        Text(row.title)
                        Button(onClick = onClick) { Text("Go") }
                    }
                }
            }
        }
    }
}

class OrderSpec : FunSpec({
    test("adds up") {
        (1 + 1) shouldBe 2
    }
})
