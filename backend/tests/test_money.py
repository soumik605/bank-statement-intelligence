# backend/tests/test_money.py
import pytest

from app.utils.money import parse_amount, split_signed


def test_indian_grouping():
    assert parse_amount("2,45,000.50") == 245000.5
    assert parse_amount("1,23,45,678.00") == 12345678.0


def test_western_grouping():
    assert parse_amount("245,000.50") == 245000.5


def test_plain_numbers():
    assert parse_amount("500") == 500.0
    assert parse_amount("0.75") == 0.75
    assert parse_amount(".50") == 0.5


def test_negative():
    assert parse_amount("-1,200.00") == -1200.0


@pytest.mark.parametrize("text", ["1,200.00 Dr", "1,200.00 DR", "1,200.00Dr", "1,200.00 dr.", "-1,200.00 Dr"])
def test_dr_suffix(text):
    assert parse_amount(text) == -1200.0


@pytest.mark.parametrize("text", ["1,200.00 Cr", "1,200.00CR", "+1,200.00"])
def test_cr_suffix_and_plus_are_positive(text):
    assert parse_amount(text) == 1200.0


def test_parentheses_negative():
    assert parse_amount("(1,200.00)") == -1200.0


def test_trailing_minus():
    assert parse_amount("1,200.00-") == -1200.0


@pytest.mark.parametrize("text", ["₹1,200.00", "₹ 1,200.00", "INR 1,200.00", "Rs. 1,200.00", "Rs 1,200.00", "rs.1,200.00"])
def test_currency_markers(text):
    assert parse_amount(text) == 1200.0


@pytest.mark.parametrize("text", ["", "   ", "-", "--", "—", None, float("nan"), "nan"])
def test_empty(text):
    assert parse_amount(text) is None


def test_numeric_input():
    assert parse_amount(1200) == 1200.0
    assert parse_amount(-99.999) == -100.0


def test_rounds_half_up_to_two_decimals():
    assert parse_amount("1.005") == 1.01
    assert parse_amount("2.675") == 2.68


def test_zero_is_not_negative():
    assert str(parse_amount("-0.00")) == "0.0"


@pytest.mark.parametrize("text", ["abc", "12.34.56", "1,2a0", "Rs.", "Dr", "1-200"])
def test_rejects_non_amounts(text):
    with pytest.raises(ValueError):
        parse_amount(text)


def test_split_signed():
    assert split_signed(-1200.0) == (1200.0, "debit")
    assert split_signed(85000.0) == (85000.0, "credit")
