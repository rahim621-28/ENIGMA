from service import most_recent_event


def test_returns_last_event():
    assert most_recent_event(["a", "b", "c"]) == "c"


def test_empty_list_returns_none():
    assert most_recent_event([]) is None
