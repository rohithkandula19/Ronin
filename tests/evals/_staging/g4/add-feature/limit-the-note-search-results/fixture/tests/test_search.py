from notebook.search import search_notes

NOTES = [
    {"title": "beta", "body": "the db is slow"},
    {"title": "alpha", "body": "nothing here"},
    {"title": "gamma", "body": "DB migration notes"},
]


def test_matches_the_body_case_insensitively():
    assert [note["title"] for note in search_notes(NOTES, "db")] == ["beta", "gamma"]


def test_results_are_title_ordered():
    assert [note["title"] for note in search_notes(NOTES, "e")] == ["alpha", "beta"]


def test_an_empty_query_matches_nothing():
    assert search_notes(NOTES, "   ") == []
