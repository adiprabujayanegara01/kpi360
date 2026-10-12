"""Mesin perhitungan KPI - Python murni (tanpa Flask/SQLAlchemy) agar mudah diuji.

Seluruh angka memakai Decimal. Pembulatan 2 desimal (round half up) hanya dilakukan
sekali, pada skor akhir (dan saat ditampilkan).
"""
from decimal import Decimal as D, ROUND_HALF_UP, InvalidOperation

HIGHER, LOWER, EXACT, BOOLEAN = "HIGHER", "LOWER", "EXACT", "BOOLEAN"
KPI_TYPES = (HIGHER, LOWER, EXACT, BOOLEAN)
HUNDRED = D("100")


class KpiError(ValueError):
    """Input KPI tidak valid (target/realisasi/bobot)."""


def to_decimal(value, field="nilai"):
    if isinstance(value, D):
        return value
    if isinstance(value, bool):
        return D(int(value))
    try:
        return D(str(value).strip().replace(",", "."))
    except (InvalidOperation, AttributeError):
        raise KpiError(f"{field} bukan angka yang valid: {value!r}")


def q2(x):
    return D(x).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def parse_realization(raw, kind, allow_negative=False):
    """Ubah input pengguna menjadi Decimal. Boolean menerima ya/tidak/yes/no/1/0."""
    if kind == BOOLEAN:
        s = str(raw).strip().lower()
        if s in ("ya", "yes", "y", "true", "1", "tercapai"):
            return D(1)
        if s in ("tidak", "no", "n", "false", "0", "belum"):
            return D(0)
        raise KpiError("Nilai boolean harus Ya atau Tidak.")
    val = to_decimal(raw, "Realisasi")
    if val < 0 and not allow_negative:
        raise KpiError("Realisasi tidak boleh negatif.")
    return val


def validate_target(kind, target):
    target = to_decimal(target, "Target")
    if kind in (HIGHER, EXACT) and target <= 0:
        raise KpiError("Target harus lebih dari 0 untuk tipe Higher/Exact.")
    if kind == LOWER and target < 0:
        raise KpiError("Target tidak boleh negatif.")
    return target


def achievement(kind, target, real, cap=HUNDRED, lower_penalty=HUNDRED, allow_negative=False):
    """Hitung achievement (0..cap) sesuai tipe KPI. Tidak dibulatkan."""
    if kind not in KPI_TYPES:
        raise KpiError(f"Tipe KPI tidak dikenal: {kind}")
    cap = to_decimal(cap, "Cap")
    target = to_decimal(target, "Target")
    real = to_decimal(real, "Realisasi")
    lower_penalty = to_decimal(lower_penalty, "Penalti")
    if real < 0 and not allow_negative:
        raise KpiError("Realisasi tidak boleh negatif.")

    if kind == BOOLEAN:
        a = HUNDRED if real else D(0)
    elif kind == HIGHER:
        if target <= 0:
            raise KpiError("Target harus lebih dari 0 untuk tipe Higher.")
        a = real / target * 100
    elif kind == EXACT:
        if target <= 0:
            raise KpiError("Target harus lebih dari 0 untuk tipe Exact.")
        a = HUNDRED - abs(real - target) / target * 100
    else:  # LOWER
        if real == 0:
            a = cap  # hasil terbaik; menghindari pembagian nol
        elif target == 0:
            a = HUNDRED - real * lower_penalty
        else:
            a = target / real * 100
    return max(D(0), min(a, cap))


def compute_score(items):
    """items: iterable of (achievement, weight_percent). Mengembalikan skor akhir (Decimal 2dp).

    BR-01: total bobot harus tepat 100.
    """
    items = [(to_decimal(a), to_decimal(w)) for a, w in items]
    total_w = sum((w for _, w in items), D(0))
    if total_w != HUNDRED:
        raise KpiError(f"Total bobot harus 100%, saat ini {total_w}%.")
    total = sum((a * w / 100 for a, w in items), D(0))
    return q2(total)


def categorize(score, categories):
    """categories: iterable of (name, min_score, max_score, color). Pilih kategori tertinggi
    yang batas bawahnya <= skor (tahan terhadap celah antar rentang, mis. 89,99-90)."""
    score = to_decimal(score)
    ordered = sorted(categories, key=lambda c: to_decimal(c[1]), reverse=True)
    for c in ordered:
        if score >= to_decimal(c[1]):
            return c[0]
    return ordered[-1][0] if ordered else None
