"""Command-line access to the same chat path the API uses.

    finqon-cli ask "How much did I spend on eating out last month?" --user EMAIL
    finqon-cli reference --user EMAIL

``ask`` runs the full agent (model, guard, executor) and prints the SQL, the result table
and the answer. ``reference`` runs the known-good dining query with no model at all, so
the two numbers can be compared by eye.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from collections.abc import Sequence

from finqon.agent.few_shot_examples import DINING_LAST_MONTH
from finqon.core.config import ConfigurationError, Settings, get_settings
from finqon.core.logging import configure_logging
from finqon.llm.client import ChatResult
from finqon.repositories.users import UserRepository
from finqon.resources import Resources
from finqon.schemas.chat import (
    ChatEvent,
    ClarificationEvent,
    DoneEvent,
    ErrorEvent,
    SqlEvent,
    TableEvent,
    TokenEvent,
)
from finqon.services.chat import ChatService, gate_message
from finqon.services.embedding_backfill import backfill_embeddings

DEFAULT_QUESTION = "How much did I spend on eating out last month?"
MAX_PRINTED_ROWS = 20


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        settings = get_settings()
        settings.require_runtime_settings(require_llm=args.command in ("ask", "embed-backfill"))
    except ConfigurationError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    configure_logging("WARNING", stream=sys.stderr)
    try:
        return asyncio.run(_run(args, settings))
    except KeyboardInterrupt:
        return 130


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="finqon-cli", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    ask = commands.add_parser("ask", help="Ask the agent a question as a seeded or real user.")
    ask.add_argument("question", nargs="?", default=DEFAULT_QUESTION)
    ask.add_argument("--user", required=True, help="Email of an existing user.")
    ask.add_argument("--conversation", type=uuid.UUID, help="Continue an existing conversation.")

    reference = commands.add_parser(
        "reference", help="Run the reference dining query directly (no model)."
    )
    reference.add_argument("--user", required=True, help="Email of an existing user.")

    commands.add_parser(
        "embed-backfill", help="Embed transaction descriptions that have no embedding yet."
    )
    return parser


class _NoModel:
    """Stands in for the model in commands that must never call it."""

    async def chat(self, *_: object, **__: object) -> ChatResult:
        raise RuntimeError("this command makes no model calls")


async def _run(args: argparse.Namespace, settings: Settings) -> int:
    resources = await Resources.create(
        settings, llm=_NoModel() if args.command == "reference" else None
    )
    try:
        if args.command == "embed-backfill":
            print(f"embedded {await backfill_embeddings(resources)} transactions")
            return 0
        user_id = await _find_user(resources, args.user)
        if user_id is None:
            print(f"no user with email {args.user!r}", file=sys.stderr)
            return 1
        if args.command == "reference":
            return await _reference(resources, user_id)
        return await _ask(resources, user_id, args.question, args.conversation)
    finally:
        await resources.close()


async def _find_user(resources: Resources, email: str) -> uuid.UUID | None:
    async with resources.database.session() as session:
        user = await UserRepository(session).get_by_email(email.strip().lower())
    return user.id if user else None


async def _reference(resources: Resources, user_id: uuid.UUID) -> int:
    result = await resources.executor.run(DINING_LAST_MONTH, user_id)
    print("-- reference SQL (no model)")
    print(DINING_LAST_MONTH)
    print()
    print(_format_table([c.name for c in result.columns], result.rows))
    return 0


async def _ask(
    resources: Resources, user_id: uuid.UUID, question: str, conversation_id: uuid.UUID | None
) -> int:
    service = ChatService(resources)
    gate = gate_message(question)
    question = gate.text
    conversation = await service.open_conversation(user_id, conversation_id, question)
    exit_code = 0
    answered_inline = False

    async for event in service.stream_turn(user_id, conversation, question, gate):
        exit_code = max(exit_code, _render(event))
        answered_inline = answered_inline or isinstance(event, TokenEvent)
    if answered_inline:
        print()
    return exit_code


def _render(event: ChatEvent) -> int:
    """Print one event. Returns a non-zero exit code only for errors."""
    match event:
        case SqlEvent():
            print(f"-- {event.purpose}")
            print(event.sql)
            print()
        case TableEvent():
            print(_format_table([c.name for c in event.columns], event.rows))
            if event.truncated:
                print(f"(truncated at {len(event.rows)} rows)")
            print()
        case ClarificationEvent():
            options = " / ".join(event.options)
            print(f"? {event.question}" + (f"  [{options}]" if options else ""))
        case TokenEvent():
            print(event.text, end="", flush=True)
        case DoneEvent():
            usage = event.usage
            print(
                f"\n[conversation {event.conversation_id} | "
                f"{usage.prompt_tokens}+{usage.completion_tokens} tokens]",
                file=sys.stderr,
            )
        case ErrorEvent():
            print(f"\nerror [{event.code}]: {event.message}", file=sys.stderr)
            return 1
        case _:
            pass  # status and chart events are for the UI
    return 0


def _format_table(columns: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    shown = [
        ["" if cell is None else _cell(cell) for cell in row] for row in rows[:MAX_PRINTED_ROWS]
    ]
    widths = [
        max(len(name), *(len(row[i]) for row in shown)) if shown else len(name)
        for i, name in enumerate(columns)
    ]
    header = "  ".join(name.ljust(width) for name, width in zip(columns, widths, strict=True))
    rule = "  ".join("-" * width for width in widths)
    lines = [header, rule]
    lines += ["  ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)) for row in shown]
    lines = [line.rstrip() for line in lines]
    if len(rows) > MAX_PRINTED_ROWS:
        lines.append(f"... {len(rows) - MAX_PRINTED_ROWS} more rows")
    return "\n".join(lines)


def _cell(value: object) -> str:
    if isinstance(value, float):
        return f"{value:,.2f}"
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
