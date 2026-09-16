from splight.splight_spec import linear_layer_8, shift_left_2_nibbles, shift_right_2_nibbles


def test_shift_inverse():
    v = list(range(8))
    assert shift_right_2_nibbles(shift_left_2_nibbles(v)) == v


def test_linear_layer_formula():
    v = [0, 1, 2, 3, 4, 5, 6, 7]
    assert linear_layer_8(v) == [
        0 ^ 2 ^ 3,
        0,
        1 ^ 2,
        0 ^ 2,
        4 ^ 6 ^ 7,
        4,
        5 ^ 6,
        4 ^ 6,
    ]
