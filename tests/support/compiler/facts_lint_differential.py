"""Shared test helpers extracted from test_facts_lint_differential_unit."""

import ast
import random

from support.paths import TESTS_ROOT

TESTS = TESTS_ROOT

FEEDERS = [
    "acceptance/test_t2_facts.py",
    "acceptance/test_t3_lint.py",
    "acceptance/test_t3_multisource.py",
    "acceptance/test_t5_grounding.py",
    "acceptance/test_t5b_blocks.py",
    "acceptance/test_t5c_structure.py",
    "acceptance/test_w3_gate.py",
    "acceptance/test_t4_coverage_guard.py",
    "unit/compiler/test_facts_unit.py",
    "unit/compiler/test_lint_unit.py",
    "unit/compiler/test_fact_matching_invariant.py",
    "unit/compiler/test_lint_speed_unit.py",
    "unit/compiler/test_gate_unit.py",
]


def literals() -> list[str]:
    out = []
    for name in FEEDERS:
        path = TESTS / name
        if not path.exists():
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                out.append(node.value)
    return sorted(set(out))


ARABIC, FULLWIDTH, DEVANAGARI = (
    "".join(map(chr, range(c, c + 10))) for c in (0x660, 0xFF10, 0x966)
)

SYMBOLS = [chr(c) for c in range(0x2010, 0x2E00) if not chr(c).isalnum() and not chr(c).isspace()]

SYMBOLS[:3] = "-/."  # ASCII symbols too: "-x", "/x", ".x"


def arabic(number: int) -> str:
    return "".join(ARABIC[int(c)] for c in str(number))


def dense(n: int, base: int = 1000) -> str:
    return " ".join(str(base + i) for i in range(n))


HOSTILE = {
    "numbers": lambda n: dense(n),
    "items": lambda n: "\n\n".join(f"Item {1000 + i} here." for i in range(n)),
    "dots": lambda n: "a." * n,
    "dashes": lambda n: "a-" * n,
    "url_tail": lambda n: "a." * n + "://.",
    "urls": lambda n: " ".join(f"https://h.example/{i}/p.{i}" for i in range(n)),
    "url_runs": lambda n: "x://y " + "a.b-c+d" * n + "://z." + " q.r://" * n,
    "backtick_runs": lambda n: "a".join("`" * r for r in range(1, n // 8 + 2)),
    "backtick_pairs": lambda n: "`a ` `` b `` " * n,
    "ticks_one": lambda n: "`a " * n,
    "fence_line": lambda n: "`" * n + " x`\n```\ncode\n```",
    "fences": lambda n: "\n".join(["```", "x", "~~~", "> ```", "  ```"] * n),
    "commas": lambda n: "1," + ",".join(["000"] * n) + "x",
    "versions": lambda n: " ".join(f"{i % 7}.{i % 5}.{i % 3}" for i in range(n)),
    "dates": lambda n: " ".join(
        f"20{i % 30:02d}-{1 + i % 12:02d}-{1 + i % 28:02d}" for i in range(n)
    ),
    "flags": lambda n: " ".join(f"--opt-{i} -{chr(97 + i % 26)}" for i in range(n)),
    "paths": lambda n: " ".join(f"/srv/a{i % 9}/b{i}.txt ./c/{i} ~/d" for i in range(n)),
    "arabic": lambda n: " ".join(arabic(1000 + i) for i in range(n)),
    "arabic_commas": lambda n: " ".join(f"x{arabic(1)},{arabic(1000 + i)[1:]}" for i in range(n)),
    "same_word": lambda n: " ".join(f"--a-{i} `a {i}` a{i} a-{i}-b" for i in range(n)),
    "permutations": lambda n: "\n".join(
        f"`{' '.join(random.Random(i).sample('abcdef', 6))}`" for i in range(n)
    ),
    "ordered": lambda n: "\n".join(f"{i}. step {i}" for i in range(n)),
    # Values of one length around one word at one offset, found, near-missed and repeated.
    "symbols_around_x": lambda n: " ".join(
        f"`{SYMBOLS[i % 7]}{SYMBOLS[i // 7 % 7]}x` {SYMBOLS[i // 7 % 7]}x{SYMBOLS[i % 7]}"
        f" a{SYMBOLS[i % 7]}x `{SYMBOLS[i % 3]}{SYMBOLS[i % 7]}x`"
        for i in range(n)
    ),
    "symbol_before_a": lambda n: " ".join(
        f"`{SYMBOLS[i % 40]}a` a {SYMBOLS[i % 9]}a" for i in range(n)
    ),
    # Values that hold one word at many offsets and lengths, met whole, inside longer tokens,
    # and as longer values' suffixes; and one long periodic value against periodic text.
    "offsets_around_a": lambda n: " ".join(
        f"`{'-' * (i % 5)}a{'-' * (i // 5 % 5)}` -{'-' * (i % 3)}a-- xa- -ax {'-' * i}a"
        for i in range(n)
    ),
    "long_periodic": lambda n: f"`{'a-' * (n // 2)}b` " + "a-" * n + "b a-a-b",
}

DIGITS = ["0123456789", ARABIC, FULLWIDTH, DEVANAGARI]

WORDS = ["the", "a", "run", "job", "retry", "deploy", "Quill", "lumen", "tessera", "node"]

WORDS += ["x", "y", "z", "on", "at", "é", "ß"]

WORDS += [chr(0x130), chr(0xFB01)]  # capital I with a dot, the fi ligature: casefold grows them

UNITS = ["", " ", "-"]

UNIT_WORDS = ["days", "day", "tokens", "files", "min", "sec", "ms", "KB", "GB", "s", "h", "%"]

PUNCT = [*".,;:!?)(-—/\\'\"<>[]{}|*_~#@+=&", "...", " - ", "…", chr(0x60C), chr(0x66B)]


def piece(rng: random.Random) -> str:
    """One token-ish piece: a fact of some kind, a near miss, or prose."""
    digits = rng.choice(DIGITS) if rng.random() < 0.15 else DIGITS[0]

    def num(lo: int = 1, hi: int = 6) -> str:
        return "".join(rng.choice(digits) for _ in range(rng.randint(lo, hi)))

    kind = rng.randrange(22)
    if kind == 0:
        return num()
    if kind == 1:
        decimals = rng.choice(["", "." + num(1, 2)])
        return f"{rng.choice(['', '$', '€', '£'])}{num(1, 3)},{num(3, 3)}{decimals}"
    if kind == 2:
        return num() + rng.choice(UNITS) + rng.choice(UNIT_WORDS)
    if kind == 3:
        time = rng.choice(["", "T10:00", "T10:00:00Z", "T10:05:30.250+01:00", "t1"])
        return f"{num(4, 4)}-{num(2, 2)}-{num(2, 2)}{time}"
    if kind == 4:
        core = ".".join(num(1, 2) for _ in range(rng.randint(1, 4)))
        return rng.choice(["", "v", "V"]) + core + rng.choice(["", "-rc1", "-beta.2", "-", "-a.b-"])
    if kind == 5:
        return (
            rng.choice(["-", "--", "---"]) + rng.choice(WORDS) + rng.choice(["", "-x", "_y", "=1"])
        )
    if kind == 6:
        root = rng.choice(["/", "~/", "./", "../", "//", ".../"])
        return root + "/".join(rng.choice([*WORDS, num()]) for _ in range(rng.randint(1, 4)))
    if kind == 7:
        scheme = rng.choice(["https", "http", "s3", "git+ssh", "a.b-c", "1x"])
        path = "/".join(rng.choice([*WORDS, num()]) for _ in range(rng.randint(0, 3)))
        tail = rng.choice(["", "?q=1", "#f", ".", ")"])
        return f"{scheme}://{rng.choice(['h.example', 'x', ''])}/{path}{tail}"
    if kind == 8:
        return f"{rng.choice(WORDS)}@{rng.choice(WORDS)}.example"
    if kind == 9:
        ticks = "`" * rng.randint(1, 3)
        inner = " ".join(piece(rng) for _ in range(rng.randint(0, 3))) or rng.choice(WORDS)
        close = ticks if rng.random() < 0.8 else "`" * rng.randint(1, 4)
        return f"{ticks}{inner}{close}"
    if kind == 10:
        return rng.choice(PUNCT)
    if kind == 11:
        return "".join(rng.choice("`.-/:~_1aé ") for _ in range(rng.randint(1, 8)))
    if kind == 12:  # two pieces touching
        return piece(rng) + piece(rng)
    if kind == 13:
        return num() + rng.choice(["x", "k", "_", "é", ".", ".5", "a1"])
    return rng.choice(WORDS)


def line(rng: random.Random) -> str:
    seps = [" "] * 6 + ["", ", ", "; ", ". ", " (", ") ", "/", "\t"]
    parts = [piece(rng) for _ in range(rng.randint(0, 12))]
    return "".join(p + rng.choice(seps) for p in parts).rstrip(" ")


def block(rng: random.Random) -> str:
    kind = rng.randrange(10)
    if kind == 0:
        return f"{'#' * rng.randint(1, 3)} {line(rng)}"
    if kind == 1:
        return "\n".join(f"{rng.choice(['-', '*', '1.', '2)'])} {line(rng)}" for _ in range(3))
    if kind == 2:
        fence = rng.choice(["```", "~~~", "````", "> ```", "  ```"])
        body = "\n".join(line(rng) for _ in range(rng.randint(0, 3)))
        close = rng.choice([fence.strip("> "), fence, "", "```x", "   ```"])
        return f"{fence}{rng.choice(['', 'sh', 'a`b'])}\n{body}\n{close}".rstrip("\n")
    if kind == 3:
        return "\n".join(f"| {line(rng)} | {piece(rng)} |" for _ in range(rng.randint(1, 3)))
    if kind == 4:
        return "\n".join(f"> {line(rng)}" for _ in range(rng.randint(1, 3)))
    if kind == 5:
        return "\n".join(
            f"# {c}" if c.startswith("#") else c for c in [line(rng), "// " + line(rng)]
        )
    return "\n".join(line(rng) for _ in range(rng.randint(1, 3)))


def document(rng: random.Random) -> str:
    return "\n\n".join(block(rng) for _ in range(rng.randint(0, 6)))


def emphasised(rng: random.Random) -> str:
    """A line where underscores open and close emphasis, sit inside words, or both."""
    pieces = []
    for _ in range(rng.randint(1, 8)):
        inner = piece(rng)
        marks = "_" * rng.choice([1, 1, 1, 2, 3, 4])
        choice = rng.randrange(9)
        if choice == 0:
            pieces.append(f"{marks}{inner}{marks}")
        elif choice == 1:
            pieces.append(f"{marks}{inner} {rng.choice(WORDS)}{marks}")
        elif choice == 2:
            pieces.append(f"{marks}{rng.choice(WORDS)} {inner}{rng.choice(['', '.', ','])}{marks}")
        elif choice == 3:
            pieces.append(f"{rng.choice(WORDS)}_{inner}")  # inside a word: max_retries_3
        elif choice == 4:
            pieces.append(f"{inner}_{rng.choice(WORDS)}")
        elif choice == 5:
            pieces.append(f"{rng.choice(PUNCT)}{marks}{inner}{marks}{rng.choice(PUNCT)}")
        elif choice == 6:
            pieces.append(f"`{marks}{inner}{marks}`")
        else:
            pieces.append(inner)
    return rng.choice([" ", "", ". ", "\n"]).join(pieces)
