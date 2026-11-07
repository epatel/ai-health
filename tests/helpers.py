"""Shared helpers for the per-language adapter tests."""

from aihealth.engine import score_source
from aihealth.lang import adapter_for


def check_base_types(path: str, table: str) -> None:
    """Each line `declared type => base types` (`-` = unknown) holds for
    the adapter of ``path``."""
    adapter = adapter_for(path)
    for line in table.strip().splitlines():
        type_text, bases = line.rsplit(" => ", 1)
        expected = None if bases == "-" else bases.split()
        assert adapter.base_types(type_text) == expected, type_text


def findings_of(src: str, path: str, smell: str) -> list:
    """Findings of one smell when ``src`` is scored as ``path``."""
    return [f for f in score_source(src.encode(), path).findings if f.smell == smell]


# SonarSource cognitive-complexity examples plus ECMAScript constructs, as
# plain JavaScript (valid TypeScript too), run through both adapters.
ECMASCRIPT_SONAR = """function sumOfPrimes(max) {
    let total = 0;
    OUT: for (let i = 1; i <= max; ++i) {
        for (let j = 2; j < i; ++j) {
            if (i % j === 0) {
                continue OUT;
            }
        }
        total += i;
    }
    return total;
}

function getWords(n) {
    switch (n) {
        case 1: return "one";
        case 2: return "a couple";
        case 3: return "a few";
        default: return "lots";
    }
}

function seq(a, b, c, d, e, f) {
    return a && b && c || d || e && f;
}

function fact(n) {
    if (n <= 1) return 1;
    return n * fact(n - 1);
}

function elseIf(a) {
    if (a > 1) {
        return 1;
    } else if (a > 0) {
        return 2;
    } else {
        return 3;
    }
}

function handlers() {
    try {
        work();
    } catch (e) {
        recover(e);
    } finally {
        done();
    }
}

function closures(xs) {
    return xs.map((x) => {
        if (x > 0) return x;
        return -x;
    });
}

function nullish(a, b) {
    return a ?? b ?? "none";
}

function ternary(x) {
    return x > 0 ? 1 : 2;
}

function loops(n) {
    let i = 0;
    outer: while (i < n) {
        do {
            i++;
            break outer;
        } while (i < 10);
    }
}

class Walker {
    walk(n) {
        if (n === 0) return 0;
        return this.walk(n - 1);
    }
}
"""

# qualname, cyclomatic, cognitive, max nesting
ECMASCRIPT_EXPECTED = [
    ("sumOfPrimes", 4, 7, 3),
    ("getWords", 4, 1, 1),  # `default` is not a path of its own
    ("seq", 6, 3, 0),
    ("fact", 2, 2, 1),
    ("elseIf", 3, 3, 1),  # else-if unwrapped from else_clause
    ("handlers", 2, 1, 1),  # catch +1; finally adds nothing
    ("closures", 2, 2, 1),  # the if sits inside an inline callback (+1 nesting)
    ("nullish", 1, 0, 0),  # `??` is not a boolean operator
    ("ternary", 2, 1, 0),
    ("loops", 3, 4, 2),  # while +1, do +2, `break outer` +1
    ("Walker.walk", 2, 2, 1),  # `this.walk(..)` is recursion
]
