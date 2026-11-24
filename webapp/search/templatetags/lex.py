from urllib.parse import urlparse

from django import template

register = template.Library()


@register.filter
def safe_url(value):
    """Only http(s) URLs become links; source data is never trusted to be a safe href."""
    try:
        return value if value and urlparse(value).scheme in ("http", "https") else ""
    except ValueError:
        return ""


@register.filter
def pct(value):
    return f"{float(value) * 100:.0f}"


@register.filter
def meter_width(value, maximum):
    try:
        return max(1, min(100, round(float(value) / float(maximum) * 100)))
    except (ValueError, ZeroDivisionError, TypeError):
        return 1
