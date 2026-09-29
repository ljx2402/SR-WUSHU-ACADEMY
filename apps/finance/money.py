"""Money handling rules for the whole project.

* Currency: MYR, 2 decimal places (sen). Stored as NUMERIC(10, 2):
  the largest amount is RM 99,999,999.99.
* Amounts entered by people (payments, charges, refunds) must already be exact
  to the sen: RM 1.005 is rejected, not rounded.
* Amounts the system computes (quantity × unit price, percentage discounts) are
  rounded HALF-UP to the sen, once, when they are stored.
* Floats are refused outright; binary floating point is never used for money.
"""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.core.exceptions import ValidationError

CURRENCY = "MYR"
PLACES = Decimal("0.01")
ZERO = Decimal("0.00")
MAX_AMOUNT = Decimal("99999999.99")


def round_money(value):
    """Round a computed Decimal to the sen, half-up."""
    return Decimal(value).quantize(PLACES, rounding=ROUND_HALF_UP)


def to_money(value, *, field="amount", allow_zero=True):
    """Validate an entered amount and return it as a 2-place Decimal.

    Accepts Decimal, int or a numeric string. Rejects floats, non-numbers,
    NaN/infinity, negatives, more than 2 decimal places and amounts above the
    storable maximum. ``allow_zero=False`` also rejects RM 0.00.
    """
    if isinstance(value, bool) or isinstance(value, float):
        raise ValidationError(f"{field}: floating-point amounts are not accepted; use a decimal string.")
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError(f"{field}: '{value}' is not a valid amount.") from None
    if not amount.is_finite():
        raise ValidationError(f"{field}: '{value}' is not a valid amount.")
    if amount != amount.quantize(PLACES, rounding=ROUND_HALF_UP):
        raise ValidationError(f"{field}: amounts may have at most 2 decimal places (sen).")
    amount = amount.quantize(PLACES)
    if amount < ZERO:
        raise ValidationError(f"{field}: amount cannot be negative.")
    if not allow_zero and amount == ZERO:
        raise ValidationError(f"{field}: amount must be more than RM 0.00.")
    if amount > MAX_AMOUNT:
        raise ValidationError(f"{field}: amount exceeds the maximum of RM {MAX_AMOUNT:,}.")
    return amount
