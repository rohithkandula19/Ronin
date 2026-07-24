"""Big-O Guess — read a snippet, name its time complexity.

ronin shows you a hand-written code fragment and a few candidate
complexities. Pick the right Big-O, get a one-line reason why, and rack up
points for being both correct *and* quick. The question bank and the grading
rule (:data:`QUESTIONS`, :func:`check`) are pure and I/O-free so they can be
unit-tested without a terminal.
"""
from __future__ import annotations

import time
from typing import Dict, List, Union

from rich.console import Console

from ._engine import GameMeta, ask_line, header

# --- palette ---------------------------------------------------------------
TEAL = "#2dd4bf"
GOOD = "#9ece6a"
WARN = "#e0af68"
ERROR = "#f7768e"
SKY = "#7dd3fc"
MUTE = "#6b7089"


# --- the question bank -----------------------------------------------------
# Each question is a plain dict so it serialises trivially and stays easy to
# eyeball. ``code`` may be a single string or a list of lines; ``options``
# always contains ``answer``.
QUESTIONS: List[Dict[str, Union[str, List[str]]]] = [
    {
        "code": [
            "def first(items):",
            "    return items[0]",
        ],
        "answer": "O(1)",
        "options": ["O(1)", "O(n)", "O(log n)", "O(n^2)"],
        "why": "A single index lookup costs the same no matter how big the list is.",
    },
    {
        "code": [
            "def total(nums):",
            "    s = 0",
            "    for n in nums:",
            "        s += n",
            "    return s",
        ],
        "answer": "O(n)",
        "options": ["O(1)", "O(n)", "O(n log n)", "O(n^2)"],
        "why": "One pass over n elements — work grows linearly with the input.",
    },
    {
        "code": [
            "def binary_search(a, target):",
            "    lo, hi = 0, len(a) - 1",
            "    while lo <= hi:",
            "        mid = (lo + hi) // 2",
            "        if a[mid] == target: return mid",
            "        if a[mid] < target: lo = mid + 1",
            "        else: hi = mid - 1",
            "    return -1",
        ],
        "answer": "O(log n)",
        "options": ["O(1)", "O(log n)", "O(n)", "O(n log n)"],
        "why": "Each step halves the search space, so it takes log2(n) steps.",
    },
    {
        "code": [
            "def has_dup(items):",
            "    for i in range(len(items)):",
            "        for j in range(i + 1, len(items)):",
            "            if items[i] == items[j]:",
            "                return True",
            "    return False",
        ],
        "answer": "O(n^2)",
        "options": ["O(n)", "O(n log n)", "O(n^2)", "O(2^n)"],
        "why": "A nested loop over the same list compares ~n*n/2 pairs.",
    },
    {
        "code": [
            "def sort_then_use(a):",
            "    a = sorted(a)        # merge/Tim sort",
            "    return a[0], a[-1]",
        ],
        "answer": "O(n log n)",
        "options": ["O(n)", "O(n log n)", "O(n^2)", "O(log n)"],
        "why": "Comparison sorting dominates at O(n log n); the lookups are O(1).",
    },
    {
        "code": [
            "def fib(n):",
            "    if n < 2: return n",
            "    return fib(n - 1) + fib(n - 2)",
        ],
        "answer": "O(2^n)",
        "options": ["O(n)", "O(n^2)", "O(2^n)", "O(n log n)"],
        "why": "Naive recursion branches twice each call — exponential blow-up.",
    },
    {
        "code": [
            "def contains(seen, x):",
            "    return x in seen   # seen is a set",
        ],
        "answer": "O(1)",
        "options": ["O(1)", "O(n)", "O(log n)", "O(n^2)"],
        "why": "Hash-set membership is amortised constant time.",
    },
    {
        "code": [
            "def members(x, lst):",
            "    return x in lst    # lst is a list",
        ],
        "answer": "O(n)",
        "options": ["O(1)", "O(log n)", "O(n)", "O(n^2)"],
        "why": "List membership scans element by element — linear worst case.",
    },
    {
        "code": [
            "def count_down(n):",
            "    while n > 1:",
            "        print(n)",
            "        n //= 2",
        ],
        "answer": "O(log n)",
        "options": ["O(1)", "O(log n)", "O(n)", "O(n^2)"],
        "why": "Dividing n by 2 each iteration gives ~log2(n) iterations.",
    },
    {
        "code": [
            "def pairs(a, b):",
            "    out = []",
            "    for x in a:",
            "        for y in b:",
            "            out.append((x, y))",
            "    return out",
        ],
        "answer": "O(n^2)",
        "options": ["O(n)", "O(n log n)", "O(n^2)", "O(2^n)"],
        "why": "Two nested loops of size n produce n*n combinations.",
    },
    {
        "code": [
            "def merge_sort(a):",
            "    if len(a) <= 1: return a",
            "    m = len(a) // 2",
            "    left = merge_sort(a[:m])",
            "    right = merge_sort(a[m:])",
            "    return merge(left, right)",
        ],
        "answer": "O(n log n)",
        "options": ["O(n)", "O(n log n)", "O(n^2)", "O(log n)"],
        "why": "log n levels of recursion, each doing O(n) merge work.",
    },
    {
        "code": [
            "def grid_sum(matrix):  # n x n matrix",
            "    total = 0",
            "    for row in matrix:",
            "        for v in row:",
            "            total += v",
            "    return total",
        ],
        "answer": "O(n^2)",
        "options": ["O(n)", "O(n log n)", "O(n^2)", "O(1)"],
        "why": "Visiting every cell of an n x n grid touches n*n elements.",
    },
    {
        "code": [
            "def get_key(d, k):",
            "    return d.get(k)    # d is a dict",
        ],
        "answer": "O(1)",
        "options": ["O(1)", "O(log n)", "O(n)", "O(n^2)"],
        "why": "Dict lookups hash the key and jump straight to the bucket.",
    },
    {
        "code": [
            "def reverse(s):",
            "    out = ''",
            "    for ch in s:",
            "        out = ch + out",
            "    return out",
        ],
        "answer": "O(n^2)",
        "options": ["O(n)", "O(n log n)", "O(n^2)", "O(log n)"],
        "why": "Each 'ch + out' rebuilds the growing string — n copies of up to n chars.",
    },
    {
        "code": [
            "def subsets(items):",
            "    res = [[]]",
            "    for x in items:",
            "        res += [s + [x] for s in res]",
            "    return res",
        ],
        "answer": "O(2^n)",
        "options": ["O(n)", "O(n^2)", "O(n log n)", "O(2^n)"],
        "why": "There are 2^n subsets, and the result doubles every iteration.",
    },
    {
        "code": [
            "def two_loops(a):",
            "    for x in a:   # first pass",
            "        touch(x)",
            "    for x in a:   # second, separate pass",
            "        touch(x)",
        ],
        "answer": "O(n)",
        "options": ["O(1)", "O(n)", "O(n^2)", "O(log n)"],
        "why": "Two sequential (not nested) passes are 2n — still linear, O(n).",
    },
]


def _norm(text: str) -> str:
    """Lower-case and strip all whitespace so 'O(N^2)' == 'o(n^2)'."""
    return "".join(text.lower().split())


def check(question, choice: str) -> bool:
    """Is ``choice`` the right answer for ``question``?

    ``question`` may be a dict or any object exposing ``answer``/``options``.
    ``choice`` can be the option text (case/space-insensitive) or a 1-based
    index into the options (as a string). Bad input simply returns ``False``.
    """
    if isinstance(question, dict):
        answer = question["answer"]
        options = question["options"]
    else:  # namedtuple / object fallback
        answer = question.answer
        options = question.options

    if choice is None:
        return False
    choice = str(choice).strip()
    if not choice:
        return False

    # 1-based index into options?
    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(options):
            return _norm(options[idx]) == _norm(answer)
        return False

    return _norm(choice) == _norm(answer)


# Validate the bank at import time — every answer must be among its options.
for _q in QUESTIONS:
    assert _q["answer"] in _q["options"], f"answer not in options: {_q['answer']}"
del _q


def _score(correct: bool, elapsed: float) -> int:
    """Points for a single question: 100 base + up to 50 speed bonus."""
    if not correct:
        return 0
    bonus = max(0, 50 - int(elapsed * 5))  # full bonus under ~0s, fades by ~10s
    return 100 + bonus


def _play(console: Console) -> None:
    header(console, GAME)
    console.print(
        f"  [{MUTE}]I show a snippet; you name its time complexity. "
        f"Faster correct answers score more. (q to quit)[/{MUTE}]\n"
    )

    import random
    order = list(range(len(QUESTIONS)))
    random.shuffle(order)

    score = 0
    asked = 0
    correct_count = 0

    for qi in order:
        q = QUESTIONS[qi]
        code = q["code"]
        lines = code if isinstance(code, list) else str(code).splitlines()
        options = q["options"]

        console.print(f"  [{MUTE}]── question {asked + 1} ──[/{MUTE}]")
        console.print(f"  [bold {SKY}]┌─ snippet[/bold {SKY}]")
        for ln in lines:
            console.print(f"  [{SKY}]│[/{SKY}] [white]{ln}[/white]")
        console.print(f"  [bold {SKY}]└─[/bold {SKY}]")
        console.print()
        for i, opt in enumerate(options, 1):
            console.print(f"    [{TEAL}]{i}[/{TEAL}]  {opt}")
        console.print()

        start = time.monotonic()
        raw = ask_line(console, "complexity (number or q):")
        elapsed = time.monotonic() - start

        if _norm(raw) in ("q", "quit", ""):
            console.print(f"\n  [{MUTE}]The answer was {q['answer']}. Bye![/{MUTE}]")
            break

        asked += 1
        is_right = check(q, raw)
        if is_right:
            gained = _score(True, elapsed)
            score += gained
            correct_count += 1
            console.print(
                f"\n  [bold {GOOD}]✓ correct[/bold {GOOD}]  "
                f"[{MUTE}]+{gained} pts ({elapsed:.1f}s)[/{MUTE}]"
            )
        else:
            console.print(
                f"\n  [bold {ERROR}]✗ nope[/bold {ERROR}]  "
                f"[{WARN}]it's {q['answer']}[/{WARN}]"
            )
        console.print(f"  [{MUTE}]why: {q['why']}[/{MUTE}]")
        console.print(f"  [{TEAL}]score: {score}[/{TEAL}]\n")
    else:
        console.print(f"  [{GOOD}]You cleared the whole bank![/{GOOD}]")

    if asked:
        console.print(
            f"\n  [bold {TEAL}]final: {score} pts[/bold {TEAL}]  "
            f"[{MUTE}]{correct_count}/{asked} correct[/{MUTE}]"
        )


GAME = GameMeta(
    key="bigo",
    name="Big-O Guess",
    emoji="📈",
    desc="guess the time complexity",
    play=_play,
)
