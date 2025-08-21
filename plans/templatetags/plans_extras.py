from __future__ import annotations

from decimal import Decimal, InvalidOperation
from django import template

register = template.Library()


@register.filter
def feet_inches(value) -> str:
    """
    Convert a total number of inches into a friendly string like: 42 -> "3′ 6″".
    Safe for None / bad inputs (returns original on failure).
    """
    try:
        total = int(value)
    except (TypeError, ValueError):
        return value
    feet, inches = divmod(total, 12)
    return f"{feet}′ {inches}″"


@register.filter
def bath_label(value) -> str:
    """
    Format a Decimal bathrooms value:
      2.0 -> "2",  2.5 -> "2.5"
    """
    try:
        d = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return str(value)

    # If it's an integer like 2.0, show "2"
    if d == d.to_integral_value():
        return str(int(d))

    # Otherwise return a trimmed decimal (keeps 0.5 etc.)
    s = f"{d.normalize()}"
    # normalize() can produce scientific notation for large/small numbers; not our case,
    # but guard just in case.
    if "E" in s or "e" in s:
        s = f"{d:.2f}".rstrip("0").rstrip(".")
    return s
