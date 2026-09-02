from service import total_price


def test_all_numeric():
    assert total_price([{"price": 10}, {"price": 5}]) == 15


def test_mixed_str_and_numeric_prices():
    assert total_price([{"price": 10}, {"price": "5"}]) == 15
