from django import template
from menard_core.formatters import format_number, format_money

register = template.Library()


@register.filter(name='money')
def money_filter(value, decimals=2):
    """
    Formats a numeric/monetary value with comma thousands separators.
    Usage:
        {{ quote.total_amount|money }}    -> "1,250,000.50"
        {{ quote.total_amount|money:0 }}  -> "1,250,000"
    """
    try:
        dec = int(decimals)
    except (ValueError, TypeError):
        dec = 2
    return format_number(value, decimals=dec)


@register.filter(name='currency')
def currency_filter(value, currency_symbol='N$'):
    """
    Formats a numeric value with currency prefix and comma thousands separators.
    Usage:
        {{ quote.total_amount|currency }}       -> "N$ 1,250,000.50"
        {{ quote.total_amount|currency:"R" }}   -> "R 1,250,000.50"
    """
    return format_money(value, currency=currency_symbol, decimals=2)


@register.filter(name='intcomma_custom')
def intcomma_custom(value):
    """
    Formats integers or floats with comma thousands separators, preserving whatever decimals exist.
    """
    return format_number(value, decimals=2)
