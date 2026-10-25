"""PII detection and masking.

One service, three uses: screening what a user types into chat (secrets are never sent to
a model or stored), masking card and account numbers in imported statement narrations,
and masking contact details that do reach a prompt. Detection is deterministic: pattern
plus a checksum where one exists (Luhn for cards), so ordinary amounts and dates are not
mistaken for card numbers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Kind = Literal["card", "cvv", "secret", "aadhaar", "pan", "account", "phone", "email"]

# Never accepted in chat: the message is not sent to the model and is stored masked.
BLOCKING: frozenset[Kind] = frozenset({"card", "cvv", "secret", "aadhaar", "pan", "account"})

_CARD = re.compile(r"(?<![\d.])(?:\d[ -]?){12,18}\d(?![\d.])")
_CVV = re.compile(r"(?i)\b(cvv2?|cvc|security code)\b\W{0,3}(?:is\W+)?\d{3,4}\b")
_SECRET = re.compile(
    r"(?i)\b(otp|one[- ]time (?:password|code)|upi pin|atm pin|mpin|pin|password|passcode|"
    r"net ?banking (?:password|id))\b\W{0,3}(?:is\W+)?(?=[\w@#$!%^&*-]*\d)[\w@#$!%^&*-]{4,}"
)
_SECRET_SENTENCE = re.compile(
    r"(?i)\b(?:otp|one[- ]time (?:password|code)|upi pin|atm pin|mpin|password|passcode)\b"
    r"(?:\W+\w+){0,5}?\W+(?:is|was|=|:)\W*(?=[\w@#$!%^&*-]*\d)[\w@#$!%^&*-]{4,}"
)
_AADHAAR = re.compile(r"(?<![\d.])[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}(?![\d.])")
_PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
_ACCOUNT = re.compile(
    r"(?i)\b(?:a/?c|acct|account)(?:\s*(?:no|number|num|#))?\b\W{0,3}(?:is\W+)?(?:x+|\*+)?(\d{9,18})\b"
)
_PHONE = re.compile(r"(?<![\d.])(?:\+?91[ -]?)?[6-9]\d{9}(?![\d.])")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")


@dataclass(frozen=True, slots=True)
class Finding:
    kind: Kind
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class Screened:
    text: str  # the input with sensitive values replaced
    findings: tuple[Finding, ...]

    @property
    def kinds(self) -> frozenset[Kind]:
        return frozenset(f.kind for f in self.findings)

    @property
    def blocked(self) -> bool:
        return bool(self.kinds & BLOCKING)


def luhn_valid(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        n = int(char)
        if index % 2:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _find(text: str) -> list[Finding]:
    found: list[Finding] = []

    def add(kind: Kind, start: int, end: int) -> None:
        if not any(start < f.end and f.start < end for f in found):
            found.append(Finding(kind, start, end))

    for m in _CVV.finditer(text):
        add("cvv", m.start(), m.end())
    for m in _SECRET.finditer(text):
        add("secret", m.start(), m.end())
    for m in _SECRET_SENTENCE.finditer(text):
        add("secret", m.start(), m.end())
    for m in _ACCOUNT.finditer(text):
        add("account", m.start(1), m.end(1))
    for m in _CARD.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and luhn_valid(digits):
            add("card", m.start(), m.end())
    for m in _AADHAAR.finditer(text):
        add("aadhaar", m.start(), m.end())
    for m in _PAN.finditer(text):
        add("pan", m.start(), m.end())
    for m in _PHONE.finditer(text):
        add("phone", m.start(), m.end())
    for m in _EMAIL.finditer(text):
        add("email", m.start(), m.end())
    return sorted(found, key=lambda f: f.start)


def _mask(kind: Kind, value: str) -> str:
    if kind in ("card", "account", "aadhaar", "phone"):
        digits = re.sub(r"\D", "", value)
        return f"[{kind.upper()} ending {digits[-4:]}]" if kind != "phone" else "[PHONE]"
    if kind == "email":
        return "[EMAIL]"
    return f"[{kind.upper()} REMOVED]"


def screen(text: str) -> Screened:
    """Replace every sensitive value and report what was found."""
    findings = _find(text)
    out: list[str] = []
    cursor = 0
    for f in findings:
        out.append(text[cursor : f.start])
        out.append(_mask(f.kind, text[f.start : f.end]))
        cursor = f.end
    out.append(text[cursor:])
    return Screened("".join(out), tuple(findings))


def mask_narration(text: str) -> str:
    """Mask identifiers in a bank narration, keeping the last four digits.

    Used on import, so the stored text and every later prompt, embedding and table
    already carry the masked form. Emails and phone numbers in a narration are masked
    too; the merchant name is untouched.
    """
    return screen(text).text


REFUSAL_MESSAGE = (
    "For your security, please don't share card numbers, CVVs, PINs, OTPs, passwords, "
    "Aadhaar, PAN or full account numbers here. I've removed that detail and did not "
    "process it. I never need it: I answer from your uploaded statement. Ask me about "
    "your spending, merchants, categories or trends instead."
)
