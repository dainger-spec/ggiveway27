"""Подсчёт общего призового фонда из сумм спонсоров («500$», «100 USDT», «1к ₽»…)."""

import re
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation

NBSP = "\N{NO-BREAK SPACE}"
THIN_NBSP = "\N{NARROW NO-BREAK SPACE}"

_SPACES = rf"\s{NBSP}{THIN_NBSP}"
_NUMBER_RE = re.compile(rf"\d[\d{_SPACES}]*(?:[.,]\d+)*")
_THOUSANDS_RE = re.compile(r"^(?:k|к|тыс[а-яё]*\.?)(?![a-zа-яё])", re.I)

_CURRENCIES = {
    "$": "$", "usd": "$", "дол": "$", "долл": "$", "доллар": "$", "долларов": "$", "доллара": "$", "бакс": "$",
    "€": "€", "eur": "€", "евро": "€",
    "₽": "₽", "р": "₽", "руб": "₽", "рубль": "₽", "рублей": "₽", "рубля": "₽", "rub": "₽",
    "usdt": "USDT", "ton": "TON", "тон": "TON", "btc": "BTC", "eth": "ETH",
    "⭐": "⭐", "stars": "⭐", "звезд": "⭐", "звёзд": "⭐", "звезды": "⭐",
}  # fmt: skip


def _to_decimal(raw: str) -> Decimal | None:
    s = re.sub(rf"[{_SPACES}]", "", raw)
    if "," in s and "." in s:
        s = s.replace(",", "")
    for sep in (",", "."):
        parts = s.split(sep)
        if len(parts) > 1:
            # «1,000» и «1.000.000» — разделители тысяч, «1,5» — дробь.
            s = "".join(parts) if all(len(p) == 3 for p in parts[1:]) else ".".join(parts)
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def parse_amount(text: str) -> tuple[Decimal, str] | None:
    """«1 500$» → (1500, "$"); «2к USDT» → (2000, "USDT"); без числа → None."""
    m = _NUMBER_RE.search(text)
    if not m:
        return None
    value = _to_decimal(m.group())
    if value is None:
        return None

    before, after = text[: m.start()], text[m.end() :].strip()
    if _THOUSANDS_RE.match(after):
        value *= 1000
        after = _THOUSANDS_RE.sub("", after, count=1)

    words = re.sub(r"[^\w$€₽⭐]", " ", f"{before} {after}".lower()).split()
    currency = _CURRENCIES.get(words[0], words[0].upper()) if words else ""
    return value, currency


def _format(value: Decimal, currency: str) -> str:
    value = value.quantize(Decimal("0.01")).normalize()
    integer, _, frac = f"{value:f}".partition(".")
    number = f"{int(integer):,}".replace(",", THIN_NBSP) + (f",{frac}" if frac else "")
    return f"{number}{NBSP}{currency}" if currency else number


def total_prize(amounts: Iterable[str | None]) -> str | None:
    """Складывает суммы по валютам: «1 600 $ + 300 USDT». Нераспознанные суммы пропускаются."""
    totals: dict[str, Decimal] = {}
    for amount in amounts:
        parsed = parse_amount(amount or "")
        if parsed is None:
            continue
        value, currency = parsed
        totals[currency] = totals.get(currency, Decimal(0)) + value
    if not totals:
        return None
    return " + ".join(_format(v, c) for c, v in totals.items())
