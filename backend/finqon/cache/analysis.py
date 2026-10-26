"""Deterministic reading of a question: what must match before a cached answer is reusable.

Embeddings answer "do these two sentences sound alike?". For a financial assistant that is
not enough: "food in September" and "food in October" sound almost identical and mean
different numbers. So every question is also reduced, with no model call, to a *constraint
signature*: the canonical form of everything that changes the answer.

* operators: total, count, average, ranking, grouping, comparison, negation, direction of
  money (spend, income, salary, refund, debit);
* periods: months, years, quarters, financial years, dates, "last", "this", "ago"...;
* numbers: thresholds and ranks ("top 5", "above 5k" become the same canonical value);
* entities: every remaining content word (merchant, category), lightly stemmed.

Two questions are interchangeable only if their signatures are equal. The vocabulary is
deliberately closed and conservative: a word the analyser does not know counts as an entity,
so two questions that differ in an unknown word differ in signature and do not share an
entry. A miss costs one model call; a false hit costs a wrong figure.

Nothing here touches the network, the clock or the database.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

_TOKEN = re.compile(
    r"""
      (?P<iso>\d{4}-\d{1,2}-\d{1,2})
    | (?P<dmy>\d{1,2}[/-]\d{1,2}[/-]\d{4})
    | (?P<fy>fy\s?\d{2,4}(?:\s?[-/]\s?\d{2,4})?)
    | (?P<qtr>q[1-4])\b
    | (?P<amount>\d[\d,]*(?:\.\d+)?)(?:\s?(?P<scale>k|lakhs?|lacs?|crores?|cr)\b)?(?:st|nd|rd|th)?
    | (?P<word>[^\W\d_]+(?:'[^\W\d_]+)?)
    """,
    re.VERBOSE,
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
}

_NUMBER_WORDS = {
    word: str(value)
    for value, word in enumerate(
        (
            "zero",
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
            "ten",
            "eleven",
            "twelve",
            "thirteen",
            "fourteen",
            "fifteen",
            "sixteen",
            "seventeen",
            "eighteen",
            "nineteen",
            "twenty",
        )
    )
} | {"thirty": "30", "forty": "40", "fifty": "50", "hundred": "100", "thousand": "1000"}

_MONTHS = {
    name: f"{index:02d}"
    for index, names in enumerate(
        (
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sep", "sept"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ),
        start=1,
    )
    for name in names
}
_WEEKDAYS = {
    name: day
    for day, names in (
        ("mon", ("monday", "mon")),
        ("tue", ("tuesday", "tue", "tues")),
        ("wed", ("wednesday", "wed")),
        ("thu", ("thursday", "thu", "thur", "thurs")),
        ("fri", ("friday", "fri")),
        ("sat", ("saturday", "sat")),
        ("sun", ("sunday", "sun")),
    )
    for name in names
}

# Whole-phrase rules, longest first. Matched on consecutive plain words.
_PHRASES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("year", "to", "date"), "t:todate"),
    (("how", "much"), "op:sum"),
    (("how", "many"), "op:count"),
    (("number", "of"), "op:count"),
    (("more", "than"), "cmp:hi"),
    (("greater", "than"), "cmp:hi"),
    (("higher", "than"), "cmp:hi"),
    (("less", "than"), "cmp:lo"),
    (("lower", "than"), "cmp:lo"),
    (("fewer", "than"), "cmp:lo"),
    (("at", "least"), "cmp:hi_eq"),
    (("at", "most"), "cmp:lo_eq"),
    (("other", "than"), "op:not"),
    (("apart", "from"), "op:not"),
    (("so", "far"), "t:todate"),
    (("to", "date"), "t:todate"),
    (("till", "date"), "t:todate"),
    (("financial", "year"), "fy:word"),
    (("fiscal", "year"), "fy:word"),
)

_WORDS: dict[str, str] = {}


def _register(token: str, *words: str) -> None:
    for word in words:
        _WORDS[word] = token


_register("op:sum", "total", "sum", "overall", "altogether", "aggregate")
_register("op:count", "count", "often", "times", "frequency")
_register("op:avg", "average", "avg", "mean", "typical")
_register("op:median", "median")
_register(
    "rank:hi",
    "top", "highest", "largest", "biggest", "most", "maximum", "max", "greatest", "best",
)  # fmt: skip
_register(
    "rank:lo", "lowest", "smallest", "least", "minimum", "min", "cheapest", "bottom", "fewest"
)
_register("op:list", "list", "transaction", "itemize", "itemise", "each", "every", "detail")
_register("op:all", "all")
_register(
    "op:group",
    "breakdown", "split", "distribution", "trend", "monthly", "weekly", "daily", "yearly",
    "quarterly", "annually", "categorywise", "categorization",
)  # fmt: skip
_register("op:by", "by", "per")
_register(
    "op:compare",
    "compare", "comparison", "versus", "vs", "against", "difference", "differ", "change",
    "growth", "increase", "decrease",
)  # fmt: skip
_register("cmp:hi", "more", "higher", "greater", "bigger", "larger", "above", "exceeding", "exceed")
_register("cmp:lo", "less", "lower", "smaller", "below", "under", "beneath", "fewer")
_register(
    "op:not",
    "not", "no", "never", "without", "except", "excluding", "exclude", "ignore", "ignoring",
    "besides", "aside", "didn't", "don't", "doesn't", "isn't", "aren't", "wasn't", "weren't",
    "can't", "cannot", "won't",
)  # fmt: skip
_register("t:last", "last", "previous", "prior", "preceding")
_register("t:past", "past")
_register("t:this", "this", "current", "present", "ongoing")
_register("t:next", "next", "upcoming", "coming")
_register("t:recent", "recent", "recently", "latest", "lately")
_register("t:today", "today", "tonight", "now")
_register("t:yesterday", "yesterday")
_register("t:tomorrow", "tomorrow")
_register("t:todate", "ytd", "mtd", "qtd")
_register("t:ago", "ago")
_register("t:before", "before")
_register("t:after", "after")
_register("t:since", "since")
_register("t:until", "until", "till")
_register("t:between", "between")
_register("t:during", "during")
_register("t:within", "within")
_register("u:day", "day")
_register("u:week", "week", "fortnight")
_register("u:weekend", "weekend")
_register("u:month", "month")
_register("u:quarter", "quarter")
_register("u:year", "year", "annual")
_register("dir:out", "spend", "spent", "spending", "expense", "expenditure", "paid", "pay", "cost")
_register(
    "dir:in",
    "income", "earn", "earned", "earning", "received", "receive", "credit", "credited",
    "deposit", "deposited", "inflow", "cashback", "revenue",
)  # fmt: skip
_register("dir:salary", "salary", "payroll", "paycheck")
_register("dir:refund", "refund", "refunded", "reimbursement")
_register("dir:debit", "debit", "debited", "outflow", "withdrawn", "withdrawal", "withdraw")
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "my",
        "me",
        "mine",
        "i",
        "we",
        "us",
        "our",
        "you",
        "your",
        "yours",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "am",
        "do",
        "does",
        "did",
        "done",
        "have",
        "has",
        "had",
        "having",
        "can",
        "could",
        "would",
        "will",
        "should",
        "shall",
        "might",
        "must",
        "please",
        "kindly",
        "pls",
        "what",
        "whats",
        "what's",
        "which",
        "who",
        "whom",
        "whose",
        "when",
        "where",
        "why",
        "how",
        "tell",
        "show",
        "give",
        "get",
        "see",
        "find",
        "display",
        "fetch",
        "check",
        "let",
        "lets",
        "want",
        "need",
        "like",
        "know",
        "about",
        "regarding",
        "concerning",
        "for",
        "of",
        "on",
        "in",
        "at",
        "with",
        "into",
        "onto",
        "and",
        "or",
        "if",
        "then",
        "than",
        "so",
        "as",
        "just",
        "only",
        "very",
        "really",
        "also",
        "too",
        "still",
        "yet",
        "there",
        "here",
        "that",
        "any",
        "some",
        "much",
        "many",
        "lot",
        "lots",
        "amount",
        "money",
        "rupee",
        "rupees",
        "rs",
        "inr",
        "it's",
        "i'm",
        "i've",
    }
)

_PERIOD_PREFIXES = ("t:", "m:", "y:", "fy:", "q:", "d:", "u:", "wd:")
_DIRECTIONS = frozenset({"dir:out", "dir:in", "dir:salary", "dir:refund", "dir:debit"})
_NON_SUM_OPS = frozenset(
    {
        "op:count", "op:avg", "op:median", "op:list", "op:group", "op:by", "op:compare",
        "rank:hi", "rank:lo", "cmp:hi", "cmp:lo", "cmp:hi_eq", "cmp:lo_eq", "op:all",
    }
)  # fmt: skip
_ORDER_SENSITIVE = frozenset(
    {
        "cmp:hi", "cmp:lo", "cmp:hi_eq", "cmp:lo_eq", "op:not", "op:compare",
        "t:before", "t:after", "t:since", "t:until", "t:between",
    }
)  # fmt: skip
# Words that make a question depend on what was said earlier in the conversation.
_CONTEXT_WORDS = frozenset(
    {
        "it", "its", "them", "they", "their", "those", "these", "again", "instead",
        "earlier", "previously", "same", "former", "latter",
    }
)  # fmt: skip
_CONTEXT_START = re.compile(
    r"^\s*(and|also|then|but|now|so|ok|okay|what about|how about|what if|same for|same as)\b"
)
_CONTEXT_PHRASES = re.compile(
    r"\b(that one|that month|that merchant|that category|that period|that amount|that "
    r"transaction|the previous (one|question|query|answer|result)|the above|as above|"
    r"before that|after that|break(ing)? (that|it|this) down|(that|this) (down|out))\b"
)
_RELATIVE_TOKENS = frozenset(
    {
        "t:last", "t:past", "t:this", "t:next", "t:recent", "t:today", "t:yesterday",
        "t:tomorrow", "t:todate", "t:ago", "t:within", "fy:word",
    }
)  # fmt: skip
_YEAR_TOKEN = re.compile(r"^y:(19|20)\d{2}$")
_VOLATILE_SQL = re.compile(
    r"\b(?:current_date|current_timestamp|localtimestamp|localtime|clock_timestamp|"
    r"statement_timestamp|transaction_timestamp|timeofday)\b|\bnow\s*\(",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class Analysis:
    """What the cache needs to know about one question."""

    text: str  # canonical text: the exact-match key and the embedding input
    features: tuple[str, ...]  # canonical tokens, in reading order
    signature: str  # digest of the constraint signature
    time_relative: bool  # the answer depends on today's date
    context_dependent: bool  # the question only makes sense after earlier turns
    order_sensitive: bool

    @property
    def cacheable(self) -> bool:
        return not self.context_dependent and bool(self.text)


def _stem(word: str) -> str:
    word = word.strip("'")
    if word.endswith("'s"):
        word = word[:-2]
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("ses", "xes", "zes", "ches", "shes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def _number(raw: str, scale: str | None) -> str:
    try:
        value = Decimal(raw.replace(",", ""))
    except InvalidOperation:  # pragma: no cover - the pattern only yields valid numbers
        return raw
    if scale:
        value *= _SCALES[scale.lower()]
    return format(value.normalize(), "f")


def _iso(year: str, month: str, day: str) -> str:
    return f"d:{int(year):04d}-{int(month):02d}-{int(day):02d}"


def _fy(raw: str) -> str:
    years = re.findall(r"\d+", raw)
    return "fy:" + "-".join(y[-2:] for y in years)


def _raw_tokens(text: str) -> tuple[list[tuple[str, str]], list[str]]:
    """Split into ``(kind, canonical)`` pairs, and the plain surface words for ``Analysis.text``."""
    tokens: list[tuple[str, str]] = []
    surface: list[str] = []
    for match in _TOKEN.finditer(text):
        surface.append(match.group(0).strip())
        kind = match.lastgroup or ""
        if kind == "iso":
            year, month, day = match.group("iso").split("-")
            tokens.append(("tok", _iso(year, month, day)))
        elif kind == "dmy":
            day, month, year = re.split(r"[/-]", match.group("dmy"))
            tokens.append(("tok", _iso(year, month, day)))
        elif kind == "fy":
            tokens.append(("tok", _fy(match.group("fy"))))
        elif kind == "qtr":
            tokens.append(("tok", "q:" + match.group("qtr")[1]))
        elif kind in {"amount", "scale"}:
            tokens.append(("num", _number(match.group("amount"), match.group("scale"))))
        else:
            tokens.append(("word", match.group("word")))
    return tokens, surface


def _canonical(tokens: list[tuple[str, str]]) -> list[str]:
    """Map words and numbers to canonical features, dropping filler."""
    out: list[str] = []
    index = 0
    while index < len(tokens):
        kind, value = tokens[index]
        if kind == "tok":
            out.append(value)
        elif kind == "num":
            numeric = Decimal(value)
            if 1900 <= numeric <= 2100 and numeric == numeric.to_integral_value():
                out.append(f"y:{value}")
            else:
                out.append(f"n:{value}")
        else:
            consumed = _phrase_at(tokens, index)
            if consumed is not None:
                feature, length = consumed
                out.append(feature)
                index += length
                continue
            out.extend(_word_features(value))
        index += 1
    return out


def _phrase_at(tokens: list[tuple[str, str]], index: int) -> tuple[str, int] | None:
    for phrase, feature in _PHRASES:
        end = index + len(phrase)
        if end > len(tokens):
            continue
        window = tokens[index:end]
        if all(
            kind == "word" and value == word
            for (kind, value), word in zip(window, phrase, strict=True)
        ):
            return feature, len(phrase)
    return None


def _variants(word: str) -> list[str]:
    """The word and its plain singular forms, so "expenses" finds "expense"."""
    forms = [word, _stem(word)]
    if word.endswith("s"):
        forms.append(word[:-1])
    if word.endswith("es"):
        forms.append(word[:-2])
    return forms


def _word_features(word: str) -> list[str]:
    if word in _MONTHS:
        return [f"m:{_MONTHS[word]}"]
    if word in _WEEKDAYS:
        return [f"wd:{_WEEKDAYS[word]}"]
    if word in _NUMBER_WORDS:
        return [f"n:{_NUMBER_WORDS[word]}"]
    for candidate in _variants(word):
        feature = _WORDS.get(candidate)
        if feature:
            return [feature]
    if word in ("from", "to"):
        return [f"op:{word}"]  # a bare "from Rahul" matters; "from last month" is dropped later
    if word in _STOPWORDS:
        return []
    return [f"e:{_stem(word)}"]


def _drop_period_glue(features: list[str]) -> list[str]:
    """Remove "from"/"to" when they only introduce a period ("from last month", "to March")."""
    kept: list[str] = []
    for position, feature in enumerate(features):
        if feature in {"op:from", "op:to"}:
            following = features[position + 1] if position + 1 < len(features) else ""
            if following.startswith(_PERIOD_PREFIXES):
                continue
        kept.append(feature)
    return kept


def _add_implicit_sum(features: list[str]) -> list[str]:
    """ "What did I spend on food" asks for a total, same as "how much did I spend on food"."""
    present = set(features)
    if "op:sum" not in present and present & _DIRECTIONS and not present & _NON_SUM_OPS:
        return [*features, "op:sum"]
    return features


def _signature(features: list[str]) -> tuple[str, bool]:
    entities = [f for f in features if f.startswith("e:")]
    numbers = [f for f in features if f.startswith("n:")]
    ordered = (
        bool(set(features) & _ORDER_SENSITIVE)
        or len(entities) >= 2
        or len(numbers) >= 2
        or "op:from" in features
        or "op:to" in features
    )
    parts = features if ordered else sorted(set(features))
    payload = ("ordered:" if ordered else "set:") + "|".join(parts)
    digest = hashlib.sha256(payload.encode()).hexdigest()
    return digest[:32], ordered


def _context_dependent(raw: str, features: list[str]) -> bool:
    if _CONTEXT_START.search(raw) or _CONTEXT_PHRASES.search(raw):
        return True
    if any(word in _CONTEXT_WORDS for word in re.findall(r"[a-z']+", raw)):
        return True
    # Nothing that says what is being asked for: a bare period ("and December?") or a bare number.
    substantive = any(
        f.startswith(("e:", "dir:", "op:", "rank:", "cmp:")) and f not in {"op:by", "op:all"}
        for f in features
    )
    return not substantive


def _time_relative(features: list[str]) -> bool:
    present = set(features)
    if present & _RELATIVE_TOKENS:
        return True
    has_year = any(_YEAR_TOKEN.match(f) for f in features) or any(
        f.startswith("d:") for f in features
    )
    # A month or quarter with no year ("in September") is resolved against today's date.
    needs_year = any(f.startswith(("m:", "q:", "wd:")) for f in features)
    return needs_year and not has_year


def analyse(question: str) -> Analysis:
    raw = unicodedata.normalize("NFKC", question).casefold().replace("\u2019", "'")
    tokens, surface = _raw_tokens(raw)
    features = _add_implicit_sum(_drop_period_glue(_canonical(tokens)))
    signature, ordered = _signature(features)
    return Analysis(
        text=" ".join(surface),
        features=tuple(features),
        signature=signature,
        time_relative=_time_relative(features),
        context_dependent=_context_dependent(raw, features),
        order_sensitive=ordered,
    )


def sql_is_time_relative(sql: str) -> bool:
    """True when the SQL itself reads the clock, so its result changes with the date."""
    return _VOLATILE_SQL.search(sql) is not None
