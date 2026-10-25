# ruff: noqa: E501
"""Deterministic merchant, category, type and payment-mode inference for imported rows.

A cascade, cheapest and most certain first: (1) the shared merchant alias table,
(2) keyword rules, (3) Unknown Merchant / Uncategorised. Only values the schema allows
are produced. Nothing here calls a model, so imports are repeatable and testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

UNKNOWN_MERCHANT = "Unknown Merchant"

# (regex, category name, txn_type). First match wins; category names must exist in
# ledger.categories or the category is left empty.
_KEYWORD_RULES: tuple[tuple[str, str, str], ...] = (
    (r"\bsalary\b|\bpayroll\b|\bsal credit\b", "Salary", "salary"),
    (r"\bemi\b|loan repay|\bnach\b.*(loan|finance)", "Housing", "emi"),
    (r"netflix|spotify|prime video|hotstar|youtube premium|subscription", "Entertainment", "subscription"),
    (r"\batm\b|cash wdl|cash withdrawal", "Cash Withdrawal", "atm"),
    (r"charges|\bfee\b|gst on|penalty|annual fee|sms alert", "Fees & Charges", "fee"),
    (r"swiggy|zomato|restaurant|cafe|coffee|starbucks|pizza|burger|dominos|mcdonald|kfc|dining", "Dining", "purchase"),
    (r"bigbasket|blinkit|zepto|dmart|grocery|supermarket|instamart|vegetable|kirana", "Groceries", "purchase"),
    (r"uber|ola\b|rapido|metro|irctc|railway|redbus|cab\b|toll|fastag", "Transport", "purchase"),
    (r"petrol|diesel|fuel|hpcl|bpcl|iocl|indian oil|shell", "Fuel", "purchase"),
    (r"electricity|power bill|water bill|gas bill|broadband|airtel|jio|vodafone|recharge|bescom|mseb|dth", "Utilities", "purchase"),
    (r"rent\b|maintenance|society", "Housing", "purchase"),
    (r"insurance|lic\b|premium", "Insurance", "purchase"),
    (r"pharmacy|apollo|hospital|clinic|medical|diagnostic|1mg|pharmeasy", "Healthcare", "purchase"),
    (r"amazon|flipkart|myntra|ajio|nykaa|meesho|mall|store|retail", "Shopping", "purchase"),
    (r"school|college|tuition|course|udemy|coursera|exam fee", "Education", "purchase"),
    (r"bookmyshow|cinema|pvr|inox|gaming|movie", "Entertainment", "purchase"),
    (r"makemytrip|goibibo|airline|indigo|air india|hotel|booking\.com|airbnb|oyo", "Travel", "purchase"),
    (r"salon|spa\b|grooming|urban company", "Personal Care", "purchase"),
)  # fmt: skip

_TRANSFER = re.compile(r"\bself\b|own account|a/c transfer|fund transfer to|\bsweep\b", re.I)
_REFUND = re.compile(r"refund|reversal|cashback|chargeback", re.I)

_MODES: tuple[tuple[str, str], ...] = (
    (r"\bupi\b|upi/|@ok|@ybl|@paytm|@ibl|@axl", "upi"),
    (r"\bneft\b", "neft"),
    (r"\bimps\b", "imps"),
    (r"\batm\b|cash wdl", "atm"),
    (r"\bpos\b|\bcard\b|debit card|credit card|visa|mastercard|rupay", "card"),
    (r"\bnach\b|\bach\b|auto.?debit|mandate|\bemi\b", "auto_debit"),
    (r"\bcash\b|cash deposit", "cash"),
)


@dataclass(frozen=True, slots=True)
class Alias:
    pattern: str
    match_type: str
    merchant_id: int


@dataclass(frozen=True, slots=True)
class Classified:
    merchant_id: int | None
    category_id: int | None
    txn_type: str
    payment_mode: str
    is_recurring: bool


class Classifier:
    def __init__(
        self,
        aliases: list[Alias],
        merchant_default_category: dict[int, int | None],
        category_ids: dict[str, int],
        unknown_merchant_id: int | None,
    ) -> None:
        self._aliases = aliases
        self._default_category = merchant_default_category
        self._categories = category_ids
        self._unknown = unknown_merchant_id
        self._compiled = [
            (re.compile(a.pattern, re.I) if a.match_type == "regex" else None, a) for a in aliases
        ]

    def _alias(self, description: str) -> int | None:
        upper = description.upper()
        for regex, alias in self._compiled:
            if alias.match_type == "exact" and upper == alias.pattern.upper():
                return alias.merchant_id
            if alias.match_type == "contains" and alias.pattern.upper() in upper:
                return alias.merchant_id
            if regex is not None and regex.search(description):
                return alias.merchant_id
        return None

    def classify(self, description: str, direction: str) -> Classified:
        merchant_id = self._alias(description)
        category_id = self._default_category.get(merchant_id) if merchant_id else None
        txn_type = "purchase"
        recurring = False

        rule_category: str | None = None
        for pattern, category, kind in _KEYWORD_RULES:
            if re.search(pattern, description, re.I):
                rule_category, txn_type = category, kind
                break
        if category_id is None and rule_category:
            category_id = self._categories.get(rule_category)
        if txn_type in ("emi", "subscription"):
            recurring = True

        if direction == "credit":
            # Refunds need a link to the original purchase, which a file never has, so
            # every credit except salary is stored as a transfer and stays out of spend.
            txn_type = "salary" if txn_type == "salary" else "transfer"
            if txn_type == "transfer" and _REFUND.search(description):
                category_id = None
        elif txn_type == "salary" or _TRANSFER.search(description):
            txn_type = "transfer"

        if merchant_id is None:
            merchant_id = self._unknown
        return Classified(
            merchant_id, category_id, txn_type, _mode(description, txn_type), recurring
        )


def _mode(description: str, txn_type: str) -> str:
    for pattern, mode in _MODES:
        if re.search(pattern, description, re.I):
            return mode
    if txn_type == "atm":
        return "atm"
    return "netbanking"
