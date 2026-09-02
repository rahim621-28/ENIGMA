from service import get_user_role


def test_role_present():
    assert get_user_role({"name": "alex", "role": "admin"}) == "admin"


def test_role_missing_defaults_to_guest():
    assert get_user_role({"name": "alex"}) == "guest"
