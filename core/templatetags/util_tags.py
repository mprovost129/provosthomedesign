# core/templatetags/util_tags.py
from django import template
from django.utils.timezone import now

register = template.Library()

@register.filter
def tel_href(value):
    """
    Turn a phone number into a tel: URL.
    Keeps '+' if provided; strips common formatting.
    """
    s = str(value or "")
    digits = "".join(ch for ch in s if ch.isdigit() or ch == "+")
    if digits and not digits.startswith("+") and len(digits) == 11 and digits[0] == "1":
        # US 11-digit like 1XXXXXXXXXX -> tel:+1XXXXXXXXXX
        return f"tel:+{digits}"
    return f"tel:{digits}"

@register.simple_tag
def current_year():
    return now().year
