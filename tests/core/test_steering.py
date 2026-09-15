"""The steering holder: push from the keyboard, drain from the loop, read for the screen.

Small enough that the tests are about the contract rather than the code — and the
contract is what the loop and the UI both depend on: draining takes *everything*, in
order, exactly once, and reading never takes anything.
"""

from __future__ import annotations

from ronin.core.steering import Steering


def test_a_pushed_correction_is_waiting_until_it_is_drained() -> None:
    steering = Steering()
    steering.push("use pathlib")

    assert steering.pending() == ("use pathlib",)
    assert steering.drain() == ("use pathlib",)
    assert steering.pending() == ()


def test_reading_the_queue_does_not_empty_it() -> None:
    # The screen pulls this on every event of a turn. If reading consumed, the first
    # repaint would delete the user's message.
    steering = Steering()
    steering.push("one")

    assert steering.pending() == ("one",)
    assert steering.pending() == ("one",)
    assert steering.drain() == ("one",)


def test_a_drain_takes_everything_in_the_order_it_was_typed() -> None:
    # All of it, not one: two corrections typed during one turn were one thought, and
    # delivering them an iteration apart would let the model act on the first while the
    # second was still invisible.
    steering = Steering()
    steering.push("first")
    steering.push("second")
    steering.push("third")

    assert steering.drain() == ("first", "second", "third")
    assert steering.drain() == ()


def test_blank_input_is_not_a_correction() -> None:
    steering = Steering()
    steering.push("")
    steering.push("   ")
    steering.push("\n\t ")

    assert steering.pending() == ()


def test_whitespace_inside_a_real_correction_is_kept_verbatim() -> None:
    # Only *entirely* blank input is dropped. A pasted snippet keeps its shape.
    steering = Steering()
    steering.push("  use pathlib\n  not os.path  ")

    assert steering.pending() == ("  use pathlib\n  not os.path  ",)


def test_two_holders_do_not_share_a_queue() -> None:
    # The mutable default that a dataclass field would have made shared state.
    first, second = Steering(), Steering()
    first.push("mine")

    assert second.pending() == ()


def test_what_is_read_out_cannot_be_used_to_mutate_the_queue() -> None:
    steering = Steering()
    steering.push("one")
    snapshot = steering.pending()

    assert isinstance(snapshot, tuple)
    assert steering.pending() == ("one",)
    assert snapshot == ("one",)
