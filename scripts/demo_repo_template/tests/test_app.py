from app import apply_discount


def test_apply_discount():
    assert apply_discount(100.0, 20.0) == 80.0
    assert apply_discount(50.0, 10.0) == 45.0
    assert apply_discount(200.0, 0.0) == 200.0
