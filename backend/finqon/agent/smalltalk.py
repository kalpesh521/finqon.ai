"""Greetings, thanks and "what can you do" handled without the model or any SQL.

A deterministic reply is instant, costs no tokens, and cannot invent figures. Anything that
is not clearly small talk returns ``None`` and goes to the agent as usual.
"""

from __future__ import annotations

import re

_WORDS = re.compile(r"[a-z']+")
_MAX_WORDS = 7

_GREETING = {"hi", "hii", "hiii", "hello", "hey", "heyy", "hola", "namaste", "yo", "sup"}
_PHRASES_GREETING = ("good morning", "good afternoon", "good evening", "good night")
_THANKS = {"thanks", "thank", "thx", "ty", "cheers", "appreciated"}
_BYE = {"bye", "goodbye", "cya", "later"}
_HOW_ARE_YOU = re.compile(r"\bhow (are|r) (you|u)\b|\bwhat'?s up\b|\bhow'?s it going\b")
_HELP = re.compile(
    r"\bwhat can you (do|help)\b|\bhow (do|does) (this|it|you) work\b|\bwho are you\b|"
    r"\bwhat (is|are) you\b|\bhelp\b|\bwhat do you do\b|\bcapabilities\b"
)
# A message that also carries a real question is not small talk.
_QUESTION_HINT = re.compile(
    r"\b(spend|spent|transaction|merchant|categor|salary|emi|paid|pay|balance|month|week|"
    r"year|quarter|total|average|top|compare|show|list|how much|how many|which)\b"
)

GREETING_REPLY = (
    "Hello! I'm FinQon, your statement assistant. Ask me about your spending, merchants, "
    "categories or trends, or upload a statement and I'll answer from it."
)
HOW_ARE_YOU_REPLY = (
    "I'm doing well, thank you. Ready when you are: ask about your spending, merchants or "
    "monthly trends."
)
THANKS_REPLY = "You're welcome. Ask me anything else about your statement whenever you like."
BYE_REPLY = "Goodbye. Your statements stay private to your account. Come back any time."
HELP_REPLY = (
    "I answer questions about your bank transactions in plain English, and show the SQL, "
    'chart and source rows behind every answer. Try "How much did I spend on dining last '
    'month?", "Show my spending by category" or "Which merchant received the most '
    'money?". You can also upload CSV, XLSX or PDF statements with the paperclip and ask '
    "about them. I only read your data; I can't move money or change anything."
)


def smalltalk_reply(message: str) -> str | None:
    text = message.strip().lower()
    words = _WORDS.findall(text)
    if not words or len(words) > _MAX_WORDS or _QUESTION_HINT.search(text):
        return None
    if _HELP.search(text):
        return HELP_REPLY
    if _HOW_ARE_YOU.search(text):
        return HOW_ARE_YOU_REPLY
    if words[0] in _GREETING or any(p in text for p in _PHRASES_GREETING):
        return GREETING_REPLY
    if words[0] in _THANKS or (words[0] == "thank" and "you" in words):
        return THANKS_REPLY
    if words[0] in _BYE:
        return BYE_REPLY
    return None
