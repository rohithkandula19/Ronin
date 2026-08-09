from metrics.summary import summarize


def test_summarises_a_handful_of_samples():
    summary = summarize([10, 20, 30])
    assert summary["count"] == 3
    assert summary["min"] == 10
    assert summary["max"] == 30
    assert summary["mean"] == 20


def test_an_endpoint_with_no_traffic_is_all_none():
    assert summarize([]) == {"count": 0, "min": None, "max": None, "mean": None}
