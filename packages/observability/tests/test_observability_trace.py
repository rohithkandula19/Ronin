"""Tracing: spans carry caller timestamps and derive duration arithmetically."""

from __future__ import annotations

from ronin_observability import Span, Tracer


def test_span_duration_from_caller_times():
    s = Span(trace_id="t", span_id="s", name="op", start=10.0, end=13.5)
    assert s.duration() == 3.5
    assert not s.is_open()


def test_open_span_has_no_duration():
    s = Span(trace_id="t", span_id="s", name="op", start=10.0)
    assert s.duration() is None
    assert s.is_open()


def test_tracer_span_scope_records_on_exit():
    tr = Tracer()
    with tr.span(trace_id="t", span_id="s1", name="work", start=0.0) as sc:
        sc.set_attribute("k", "v")
        sc.stop(end=2.0)
    assert len(tr.spans) == 1
    rec = tr.spans[0]
    assert rec.duration() == 2.0
    assert rec.attributes["k"] == "v"


def test_scope_records_open_span_if_not_stopped():
    tr = Tracer()
    with tr.span(trace_id="t", span_id="s1", name="work", start=1.0):
        pass
    assert tr.spans[0].is_open()


def test_parent_child_relationship():
    tr = Tracer()
    parent = tr.record(
        tr.start(trace_id="t", span_id="p", name="parent", start=0.0)
    )
    child = tr.record(
        tr.start(
            trace_id="t", span_id="c", name="child", start=1.0, parent_id="p"
        )
    )
    assert child.parent_id == parent.span_id
    assert len(tr.trace_spans("t")) == 2


def test_exception_does_not_suppress_and_span_recorded():
    tr = Tracer()
    try:
        with tr.span(trace_id="t", span_id="s", name="boom", start=0.0) as sc:
            sc.stop(end=1.0)
            raise ValueError("x")
    except ValueError:
        pass
    assert len(tr.spans) == 1
    assert tr.spans[0].duration() == 1.0


def test_trace_spans_filters_by_trace_id():
    tr = Tracer()
    tr.record(tr.start(trace_id="a", span_id="1", name="n", start=0.0))
    tr.record(tr.start(trace_id="b", span_id="2", name="n", start=0.0))
    assert len(tr.trace_spans("a")) == 1
    assert len(tr.trace_spans("b")) == 1
