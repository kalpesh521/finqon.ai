"""Check that every figure in the answer came from a tool result.

The model is told to do arithmetic in SQL. After the prose is written, each number in
it must match a value the model was shown (query rows, row count, min/max/sum), a number
from the user's question, or a literal in the SQL that ran, within rounding tolerance.
ISO dates (``YYYY-MM-DD``) must appear in the same evidence text. Years used as labels,
ordinals and small counts (10 or fewer) are not treated as numeric claims.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

_MONTHS = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)

# Things that look like numbers but are dates, labels, or ordinals.
_IGNORED_PATTERNS = [
    re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b"),  # 2026-09, 2026-09-14
    re.compile(r"\bFY\s?\d{4}(?:-\d{2,4})?\b", re.IGNORECASE),
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTHS}\b(?:\s+\d{{4}})?", re.IGNORECASE),
    re.compile(rf"\b{_MONTHS}\b\.?\s+\d{{1,2}}(?:st|nd|rd|th)?\b(?:,?\s+\d{{4}})?", re.IGNORECASE),
    re.compile(rf"\b{_MONTHS}\b\.?\s+\d{{4}}\b", re.IGNORECASE),
    re.compile(r"\b\d{1,2}(?:st|nd|rd|th)\b", re.IGNORECASE),
    re.compile(r"\b\d{1,2}:\d{2}\b"),
]

_NUMBER = re.compile(
    r"""
    (?<![\w.])
    (?P<currency>₹|Rs\.?|INR)?\s?
    (?P<digits>\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)
    \s?
    (?P<suffix>%|k\b|lakhs?\b|lacs?\b|crores?\b|cr\b|million\b)?
    """,
    re.VERBOSE | re.IGNORECASE,
)

_SCALES = {
    "k": Decimal(1_000),
    "lakh": Decimal(100_000),
    "lakhs": Decimal(100_000),
    "lac": Decimal(100_000),
    "lacs": Decimal(100_000),
    "crore": Decimal(10_000_000),
    "crores": Decimal(10_000_000),
    "cr": Decimal(10_000_000),
    "million": Decimal(1_000_000),
}
_SMALL_COUNT = Decimal(10)
_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


@dataclass(frozen=True, slots=True)
class Claim:
    text: str
    value: Decimal
    tolerance: Decimal


@dataclass(frozen=True, slots=True)
class GroundingResult:
    ungrounded: list[str]

    @property
    def ok(self) -> bool:
        return not self.ungrounded


def extract_claims(text: str) -> list[Claim]:
    """Numbers in ``text`` that must be backed by a tool result."""
    cleaned = text
    for pattern in _IGNORED_PATTERNS:
        cleaned = pattern.sub(" ", cleaned)

    claims: list[Claim] = []
    for match in _NUMBER.finditer(cleaned):
        raw = match.group("digits")
        try:
            value = Decimal(raw.replace(",", ""))
        except InvalidOperation:  # pragma: no cover - the regex only yields valid numbers
            continue
        suffix = (match.group("suffix") or "").lower()
        has_currency = bool(match.group("currency"))
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        is_plain_integer = decimals == 0 and not suffix and not has_currency

        is_year = 1900 <= value <= 2100 and "," not in raw
        if is_plain_integer and (value <= _SMALL_COUNT or is_year):
            continue  # small counts and years are not financial claims

        scale = _SCALES.get(suffix, Decimal(1))
        claims.append(
            Claim(
                text=match.group(0).strip(),
                value=value * scale,
                tolerance=Decimal("0.5") * scale / (Decimal(10) ** decimals),
            )
        )
    return claims


def numbers_in(text: str) -> list[Decimal]:
    """Every numeric literal in ``text`` (question or SQL), used as allowed values."""
    values: list[Decimal] = []
    for match in re.finditer(r"(?<![\w.])\d[\d,]*(?:\.\d+)?", text):
        try:
            values.append(Decimal(match.group(0).replace(",", "")))
        except InvalidOperation:
            continue
    return values


def dates_in(text: str) -> list[str]:
    """ISO calendar dates in ``text``. These must be copied, not invented."""
    return _ISO_DATE.findall(text)


def verify_answer(
    answer: str, allowed: Iterable[Decimal], *, evidence: str = ""
) -> GroundingResult:
    allowed_values = [abs(v) for v in allowed]
    missing: list[str] = []
    for claim in extract_claims(answer):
        value = abs(claim.value)
        if not any(abs(value - candidate) <= claim.tolerance for candidate in allowed_values):
            missing.append(claim.text)
    for date in dates_in(answer):
        if date not in evidence:
            missing.append(date)
    return GroundingResult(ungrounded=missing)
