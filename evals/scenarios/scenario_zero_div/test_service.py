from service import calculate_metrics


def test_normal_division():
    assert calculate_metrics(100, 4) == 25


def test_zero_count_does_not_raise():
    assert calculate_metrics(100, 0) == 0
