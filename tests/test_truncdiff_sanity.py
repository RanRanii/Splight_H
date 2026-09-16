from truncdiff import TruncDiff


def test_xor_templates():
    td = TruncDiff(1)
    assert td.constraints_by_xor_standard("u", "v", "w") == [
        "u + v - w >= 0",
        "u - v + w >= 0",
        "- u + v + w >= 0",
    ]
    assert td.constraints_by_xor_prob_one("u", "v", "w") == [
        "w - u >= 0",
        "w - v >= 0",
        "u + v - w >= 0",
    ]


def test_round_model_contains_two_sbox_layers_and_shift_mapping(tmp_path):
    lp = tmp_path / "td.lp"
    td = TruncDiff(1, lp_file_name=str(lp))
    td.make_model()
    text = lp.read_text()
    assert "x_0_0 - y_0_0 = 0" in text
    assert "l_0_0 - z_0_0 = 0" in text
    assert "z_0_0 + x_0_8 - x_1_6 >= 0" in text
    assert "t_0_0" not in text
    assert "x_0_0 - x_1_8 = 0" in text
