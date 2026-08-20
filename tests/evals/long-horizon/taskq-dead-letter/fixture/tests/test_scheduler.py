"""Tests for taskq.scheduler."""

from taskq.policy import Policy
from taskq.scheduler import next_job
from taskq.store import Store


def test_the_first_ready_job_is_chosen():
    store = Store()
    store.add("a", "x", "1")
    store.add("b", "y", "2")
    assert next_job(store, Policy()).id == "a"


def test_an_empty_store_has_nothing_to_run():
    assert next_job(Store(), Policy()) is None
