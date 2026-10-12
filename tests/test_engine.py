from decimal import Decimal as D
import pytest
from app.kpi_engine import (achievement, compute_score, categorize, parse_realization, validate_target, KpiError)

H, L, E, B = "HIGHER", "LOWER", "EXACT", "BOOLEAN"


@pytest.mark.parametrize("kind,t,r,cap,exp", [
    (H, 100, 95, 100, "95"), (H, 100, 100, 100, "100"), (H, 100, 150, 100, "100"), (H, 100, 150, 120, "120"), (H, 100, 0, 100, "0"),
    (H, 4, 4.5, 100, "100"), (H, "4,0", "3,2", 100, "80"),
    (L, 2, 1.5, 100, "100"), (L, 2, 4, 100, "50"), (L, 2, 2, 100, "100"), (L, 5, 0, 100, "100"), (L, 5, 0, 120, "120"), (L, 2, 1, 150, "150"),
    (E, 100, 90, 100, "90"), (E, 100, 110, 100, "90"), (E, 100, 100, 100, "100"), (E, 100, 300, 100, "0"), (E, 100, 0, 100, "0"),
    (B, 1, 1, 100, "100"), (B, 1, 0, 100, "0"),
])
def test_achievement(kind, t, r, cap, exp):
    assert achievement(kind, t, r, cap) == D(exp)


def test_lower_target_zero_penalty():
    assert achievement(L, 0, 0) == D(100)
    assert achievement(L, 0, 1) == D(0)  # penalti default 100
    assert achievement(L, 0, 1, lower_penalty=25) == D(75)
    assert achievement(L, 0, 3, lower_penalty=25) == D(25)
    assert achievement(L, 0, 9, lower_penalty=25) == D(0)  # dibatasi minimum 0


@pytest.mark.parametrize("kind", [H, E])
def test_target_zero_rejected(kind):
    with pytest.raises(KpiError):
        validate_target(kind, 0)
    with pytest.raises(KpiError):
        achievement(kind, 0, 5)


def test_negative_realization():
    with pytest.raises(KpiError):
        achievement(H, 10, -1)
    with pytest.raises(KpiError):
        parse_realization("-3", H)
    assert achievement(H, 10, -5, allow_negative=True) == D(0)
    assert parse_realization("-3", H, allow_negative=True) == D(-3)


def test_parse_boolean_and_decimal_comma():
    assert parse_realization("Ya", B) == 1 and parse_realization("tidak", B) == 0
    assert parse_realization("1,5", H) == D("1.5")
    with pytest.raises(KpiError):
        parse_realization("mungkin", B)
    with pytest.raises(KpiError):
        parse_realization("abc", H)


def test_unknown_type():
    with pytest.raises(KpiError):
        achievement("X", 1, 1)


def test_example_from_design_doc():
    items = [(achievement(H, 100, 95), 30), (achievement(L, 2, 1.5), 20), (achievement(B, 1, 1), 30), (achievement(H, 4, 4.5), 20)]
    assert compute_score(items) == D("98.50")


def test_weights_must_total_100():
    with pytest.raises(KpiError):
        compute_score([(D(90), 60), (D(80), 30)])
    with pytest.raises(KpiError):
        compute_score([(D(90), 60), (D(80), 50)])


def test_rounding_once_half_up():
    # 3 KPI @ 33.335 -> total 100.005 tidak mungkin; uji pembulatan half-up pada total
    assert compute_score([(D("84.125"), 50), (D("84.125"), 50)]) == D("84.13")
    assert compute_score([(D("84.124"), 100)]) == D("84.12")


CATS = [("Sangat Baik", 90, 100, "g"), ("Baik", 80, 89.99, "b"), ("Cukup", 70, 79.99, "y"), ("Kurang", 60, 69.99, "o"), ("Sangat Kurang", 0, 59.99, "r")]


@pytest.mark.parametrize("s,c", [(100, "Sangat Baik"), (90, "Sangat Baik"), ("89.99", "Baik"), (80, "Baik"), ("79.99", "Cukup"), (70, "Cukup"),
                                 ("69.99", "Kurang"), (60, "Kurang"), ("59.99", "Sangat Kurang"), (0, "Sangat Kurang")])
def test_categorize(s, c):
    assert categorize(s, CATS) == c


def test_categorize_tolerates_gap():
    assert categorize("89.995", CATS) == "Baik"
