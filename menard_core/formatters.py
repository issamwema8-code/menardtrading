"""
Menard Trading CC — Central Monetary & Number Formatting Engine
Provides canonical formatting utilities for currency, integers, and decimals
with comma thousands separators across all Python services, templates, APIs, and reports.
"""

import decimal
from decimal import Decimal
from typing import Union, Optional


def format_number(val: Union[int, float, Decimal, str, None], decimals: int = 2, default: str = "0.00") -> str:
    """
    Formats a numeric value with comma thousands separators and specified decimal places.
    Examples:
        format_number(1000, 2)       -> "1,000.00"
        format_number(1000, 0)       -> "1,000"
        format_number(1250000.5, 2)  -> "1,250,000.50"
        format_number(0, 2)          -> "0.00"
        format_number(-1250.75, 2)   -> "-1,250.75"
    """
    if val is None or val == "":
        return default
    try:
        if isinstance(val, (int, float, str)):
            d = Decimal(str(val))
        elif isinstance(val, Decimal):
            d = val
        else:
            d = Decimal(str(val))
    except (decimal.InvalidOperation, TypeError, ValueError):
        return default

    fmt = f"{{:,.{decimals}f}}"
    return fmt.format(d)


def format_money(val: Union[int, float, Decimal, str, None], currency: Optional[str] = None, decimals: int = 2, default: str = "0.00") -> str:
    """
    Formats a monetary value with comma thousands separators and optional currency prefix.
    Examples:
        format_money(1250000.50, "N$")   -> "N$ 1,250,000.50"
        format_money(1000, "R")          -> "R 1,000.00"
        format_money(-5000, "N$")        -> "-N$ 5,000.00"
        format_money(1250000)            -> "1,250,000.00"
    """
    formatted_num = format_number(val, decimals=decimals, default=default)
    if currency:
        currency_str = str(currency).strip()
        if formatted_num.startswith('-'):
            return f"-{currency_str} {formatted_num[1:]}"
        return f"{currency_str} {formatted_num}"
    return formatted_num
