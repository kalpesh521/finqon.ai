"""LLM rubric judge. Used only for cases that carry a ``rubric``; everything else is code.

The judge sees the question, the result table as ground truth, the reply and the rubric,
and must quote evidence before each verdict. Use a different model family from the agent
(``FINQON_JUDGE_*``); when none is configured the agent's model judges itself and the
report says so, because a model tends to favour its own writing.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from finqon.evaluation.scoring import Verdict
from finqon.llm.client import LLMError, LLMProvider

_SYSTEM = (
    "You are a strict evaluator of a bank-statement assistant. You are given the user's "
    "question, the RESULT TABLE (ground truth, the only facts that exist), the assistant's "
    "REPLY, and a RUBRIC. For each rubric item, first quote the evidence from the reply or "
    "table, then decide whether it is met. A claim not supported by the table is not met. "
    'Reply with JSON only: {"items":[{"criterion":"...","evidence":"...","met":true|false}]}'
)
_MAX_ROWS = 30


def _table_text(columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    lines = [" | ".join(columns)]
    lines += [" | ".join(str(c) for c in row) for row in rows[:_MAX_ROWS]]
    if len(rows) > _MAX_ROWS:
        lines.append(f"... {len(rows) - _MAX_ROWS} more rows")
    return "\n".join(lines)


def parse_judgement(raw: str, rubric: Sequence[str]) -> Verdict:
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not match:
        return Verdict.fail("judge returned no JSON")
    try:
        items = json.loads(match.group(0)).get("items", [])
    except (json.JSONDecodeError, AttributeError):
        return Verdict.fail("judge returned invalid JSON")
    if not isinstance(items, list) or len(items) != len(rubric):
        return Verdict.fail(f"judge returned {len(items)} verdicts for {len(rubric)} criteria")
    unmet = [
        f"{item.get('criterion', '?')}: {item.get('evidence', '')}"
        for item in items
        if isinstance(item, dict) and item.get("met") is not True
    ]
    return Verdict.fail("rubric not met: " + "; ".join(unmet)) if unmet else Verdict.ok()


async def rubric_judge(
    llm: LLMProvider,
    *,
    question: str,
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
    reply: str,
    rubric: Sequence[str],
) -> Verdict:
    user = (
        f"QUESTION:\n{question}\n\nRESULT TABLE:\n{_table_text(columns, rows)}\n\n"
        f"REPLY:\n{reply}\n\nRUBRIC:\n" + "\n".join(f"- {item}" for item in rubric)
    )
    try:
        result = await llm.chat(
            [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}], None
        )
    except LLMError as exc:
        return Verdict.fail(f"judge unavailable: {exc}")
    return parse_judgement(result.content or "", rubric)
