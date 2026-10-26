"""Check that the semantic cache works on a running API.

    python eval/verify_cache.py --email kalpeshpawar@gmail.com --password finqon-seed-local

It asks the same question twice, then a reworded version, then the same question as the
other seed user, and prints what the cache did each time. A real model call is made for the
first ask, so this spends a little model quota. Exit code 0 means every check passed.

The cache must be ``on``. In ``shadow`` mode nothing is ever served from it, so the repeat
checks are reported as skipped and only the shadow counters are checked.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

import httpx

QUESTION = "How much did I spend on food last month?"
REWORDED = "what was my total spending on food in the previous month"
DIFFERENT = "How much did I spend on food in the month before last?"


def login(client: httpx.Client, email: str, password: str) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def ask(client: httpx.Client, headers: dict[str, str], question: str) -> dict[str, Any]:
    started = time.perf_counter()
    with client.stream(
        "POST", "/api/v1/chat", headers=headers, json={"message": question}, timeout=120
    ) as response:
        response.raise_for_status()
        body = "".join(response.iter_text())
    events = []
    for frame in body.strip().split("\n\n"):
        data = "".join(line[5:].strip() for line in frame.splitlines() if line.startswith("data:"))
        if data:
            events.append(json.loads(data))
    done = next((e for e in events if e["type"] == "done"), {})
    return {
        "seconds": time.perf_counter() - started,
        "tokens": (done.get("usage") or {}).get("total_tokens"),
        "sql": next((e["sql"] for e in events if e["type"] == "sql"), None),
        "errors": [e for e in events if e["type"] == "error"],
        "answer": "".join(e["text"] for e in events if e["type"] == "token").strip(),
    }


def status(client: httpx.Client) -> dict[str, Any]:
    response = client.get("/health/cache")
    response.raise_for_status()
    return response.json()


class Checks:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def __call__(self, ok: bool, label: str) -> None:
        print(f"  [{'ok' if ok else 'FAIL'}] {label}")
        if not ok:
            self.failures.append(label)


def show(label: str, turn: dict[str, Any], note: str = "") -> None:
    print(f"\n{label}\n     {turn['seconds']:.2f}s, tokens={turn['tokens']} {note}".rstrip())


def exercise(
    client: httpx.Client, args: argparse.Namespace, check: Checks, *, serving: bool
) -> None:
    headers = login(client, args.email, args.password)

    first = ask(client, headers, QUESTION)
    show("1. first ask (the agent answers, the plan is stored)", first)
    check(not first["errors"], "no error event")

    second = ask(client, headers, QUESTION)
    show("2. same question again (expected: served from the cache, 0 tokens)", second)
    if serving:
        check(second["tokens"] == 0, "no model tokens were spent")
        check(second["answer"] == first["answer"], "same answer text")
        check(second["sql"] == first["sql"], "same SQL")
        if first["tokens"]:  # a re-run of this script finds the first ask already cached
            check(second["seconds"] < first["seconds"], "faster than the first ask")
    else:
        print("     skipped: shadow mode never serves")

    show(
        "3. reworded question (a semantic hit if the embedding is close enough)",
        ask(client, headers, REWORDED),
        "(0 tokens means a semantic hit)",
    )

    fourth = ask(client, headers, DIFFERENT)
    show("4. a different period (expected: NOT a hit; the agent runs)", fourth)
    if serving:
        check(bool(fourth["tokens"]), "a different period was not answered from the cache")

    if args.other_email:
        other_headers = login(client, args.other_email, args.other_password or "")
        other = ask(client, other_headers, QUESTION)
        show("5. the same question as another user (expected: NOT a hit)", other)
        if serving:
            check(bool(other["tokens"]), "another user never receives this user's answer")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--other-email", help="a second user, to prove answers are not shared")
    parser.add_argument("--other-password")
    args = parser.parse_args()
    check = Checks()

    with httpx.Client(base_url=args.url) as client:
        before = status(client)
        print(f"cache: mode={before['mode']} enabled={before['enabled']}", end="")
        if not before["enabled"]:
            print("\nThe cache is off. Set FINQON_CACHE_MODE=on (or shadow) and restart.")
            return 2
        print(f" backend={before['backend']} store_reachable={before['store_reachable']}")
        check(before["store_reachable"], "the cache store answers")
        serving = before["mode"] == "on"

        exercise(client, args, check, serving=serving)

        after = status(client)["counters"]
        delta = {k: after.get(k, 0) - before["counters"].get(k, 0) for k in after}
        print("\ncounter changes:", {k: v for k, v in delta.items() if v})
        check(delta.get("lookup", 0) >= 3, "lookups were counted")
        if serving:
            check(delta.get("served", 0) >= 1, "at least one answer was served from the cache")
            check(delta.get("replay_stale", 0) == 0, "no stale replays")
            check(delta.get("replay_ungrounded", 0) == 0, "no ungrounded replays")

    print("\nPASS" if not check.failures else f"\nFAIL: {len(check.failures)} check(s) failed")
    return 1 if check.failures else 0


if __name__ == "__main__":
    sys.exit(main())
