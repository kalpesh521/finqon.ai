from __future__ import annotations

import pytest

from finqon.agent.smalltalk import smalltalk_reply
from finqon.privacy.pii import luhn_valid, mask_narration, screen
from finqon.services.chat import gate_message

VISA = "4111 1111 1111 1111"  # a standard test number that passes Luhn


def test_luhn() -> None:
    assert luhn_valid("4111111111111111")
    assert not luhn_valid("4111111111111112")


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        (f"my card is {VISA}", "card"),
        ("cvv 123", "cvv"),
        ("my otp is 482913", "secret"),
        ("upi pin is 1234", "secret"),
        ("aadhaar 2345 6789 0123", "aadhaar"),
        ("PAN ABCDE1234F", "pan"),
        ("account number 123456789012", "account"),
    ],
)
def test_sensitive_values_are_found_and_blocked(text: str, kind: str) -> None:
    result = screen(text)
    assert kind in result.kinds and result.blocked
    assert not any(ch.isdigit() for ch in result.text.replace("ending", "")) or kind in (
        "card",
        "account",
        "aadhaar",
    )


def test_card_mask_keeps_only_the_last_four() -> None:
    masked = screen(f"pay with {VISA} please").text
    assert "4111 1111" not in masked and "ending 1111" in masked


def test_ordinary_numbers_are_not_pii() -> None:
    for text in [
        "How much did I spend in 2026?",
        "Show transactions above ₹5,000.00 in the last 30 days",
        "spent 4111111111111112 rupees",  # fails Luhn
        "total 1234567890.50",
    ]:
        assert not screen(text).blocked, text


def test_contact_details_are_masked_but_not_blocked() -> None:
    result = screen("send it to me@example.com or 9876543210")
    assert not result.blocked and "[EMAIL]" in result.text and "[PHONE]" in result.text


def test_narrations_are_masked_on_import() -> None:
    out = mask_narration(f"POS {VISA} AMAZON ACCOUNT NO 123456789012")
    assert "1111 1111" not in out and "123456789012" not in out and "AMAZON" in out


def test_gate_refuses_secrets_professionally_without_the_model() -> None:
    gate = gate_message(f"my card number is {VISA}, what did I spend?")
    assert gate.reply and "don't share" in gate.reply and VISA not in gate.text


@pytest.mark.parametrize(
    "text", ["Hi", "hello!", "Hey there", "good morning", "thanks", "Thank you!", "bye"]
)
def test_small_talk_gets_a_fixed_reply(text: str) -> None:
    assert smalltalk_reply(text)


@pytest.mark.parametrize(
    "text",
    [
        "hi, how much did I spend on dining?",
        "Show my spending by category",
        "thanks, now top merchants",
    ],
)
def test_real_questions_are_not_small_talk(text: str) -> None:
    assert smalltalk_reply(text) is None


def test_what_can_you_do_explains_capabilities() -> None:
    reply = smalltalk_reply("what can you do?")
    assert reply and "statement" in reply


def test_otp_inside_a_sentence_is_blocked() -> None:
    assert screen("The OTP for my bank login is 482913, check my balance").blocked
    assert not screen("What is my password policy spend on Netflix?").blocked
