import { describe, expect, it } from "vitest";

import { parseChatEvent } from "@/api/eventSchema";
import { parseSseFrame, takeFrames } from "@/api/chatStream";
import { chartPoints, sumValues } from "@/lib/format";
import { groupConversations } from "@/lib/history";
import { chatReducer, displayedChart, initialChatState, type ChatState } from "@/state/chatReducer";

const MESSAGE = "11111111-1111-4111-8111-111111111111";
const CONVERSATION = "22222222-2222-4222-8222-222222222222";

function start(question = "How much did I spend on eating out?"): ChatState {
    return chatReducer(initialChatState, {
        type: "start",
        localId: "local-1",
        question,
        createdAt: "2026-10-21T08:00:00.000Z",
    });
}

describe("chat stream parsing", () => {
    it("splits frames and keeps a partial frame", () => {
        const taken = takeFrames('event: status\ndata: {"type":"status"}\n\ndata: {"type":"token"');
        expect(taken.frames).toEqual(['event: status\ndata: {"type":"status"}']);
        expect(taken.rest).toBe('data: {"type":"token"');
    });

    it("parses an SSE data payload", () => {
        expect(parseSseFrame('event: token\ndata: {"type":"token","text":"Hi"}')).toEqual({
            type: "token",
            text: "Hi",
        });
    });

    it("rejects an event that is not in the contract", () => {
        expect(() => parseChatEvent({ type: "token" })).toThrow();
        expect(() => parseChatEvent({ type: "invented" })).toThrow();
    });
});

describe("chat reducer", () => {
    it("handles every event and plots table rows, not the sentence", () => {
        let state = start();
        state = chatReducer(state, {
            type: "event",
            event: parseChatEvent({ type: "status", stage: "thinking" }),
        });
        state = chatReducer(state, {
            type: "event",
            event: parseChatEvent({
                type: "sql",
                sql: "SELECT category_name, SUM(spend_amount) AS total FROM reporting.spend GROUP BY 1",
                purpose: "Spend by category",
            }),
        });
        state = chatReducer(state, {
            type: "event",
            event: parseChatEvent({ type: "status", stage: "querying" }),
        });
        const rows = [
            ["Dining", 8642.3],
            ["Groceries", 2158.4],
        ];
        state = chatReducer(state, {
            type: "event",
            event: parseChatEvent({
                type: "table",
                columns: [
                    { name: "category_name", type: "string" },
                    { name: "total", type: "number" },
                ],
                rows,
                truncated: false,
                elapsed_ms: 42,
            }),
        });
        state = chatReducer(state, {
            type: "event",
            event: parseChatEvent({ type: "chart", kind: "bar", x: "category_name", y: "total" }),
        });
        state = chatReducer(state, {
            type: "event",
            event: parseChatEvent({ type: "status", stage: "summarizing" }),
        });
        const beforeTokens = state.evidence;
        state = chatReducer(state, {
            type: "event",
            event: parseChatEvent({ type: "token", text: "You spent ₹999999 on nothing." }),
        });
        expect(state.evidence).toBe(beforeTokens);
        expect(state.turns[0]?.answer).toBe("You spent ₹999999 on nothing.");
        state = chatReducer(state, {
            type: "event",
            event: parseChatEvent({
                type: "done",
                message_id: MESSAGE,
                conversation_id: CONVERSATION,
                usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 },
                status: "answered",
            }),
        });

        expect(state.streaming).toBe(false);
        expect(state.conversationId).toBe(CONVERSATION);
        expect(state.evidence?.rows).toEqual(rows);
        expect(displayedChart(state)).toBe("bar");
        const points = chartPoints(
            state.evidence?.columns ?? [],
            state.evidence?.rows ?? [],
            "category_name",
            "total",
        );
        expect(points.map((point) => point.value)).toEqual([8642.3, 2158.4]);
        expect(sumValues(points)).toBeCloseTo(10800.7);

        state = chatReducer(state, { type: "chart", kind: "pie" });
        expect(displayedChart(state)).toBe("pie");
        expect(state.evidence?.rows).toEqual(rows);
        state = chatReducer(state, { type: "chart", kind: "line" });
        expect(displayedChart(state)).toBe("line");
        expect(state.evidence?.rows).toEqual(rows);
    });

    it("keeps two queries on the turn and plots the latest rows", () => {
        let state = start();
        state = chatReducer(state, {
            type: "event",
            event: parseChatEvent({ type: "sql", sql: "SELECT 1 AS total", purpose: "First" }),
        });
        state = chatReducer(state, {
            type: "event",
            event: parseChatEvent({
                type: "table",
                columns: [{ name: "total", type: "number" }],
                rows: [[10]],
                truncated: false,
                elapsed_ms: 5,
            }),
        });
        state = chatReducer(state, {
            type: "event",
            event: parseChatEvent({ type: "chart", kind: "none", x: "", y: "" }),
        });
        state = chatReducer(state, {
            type: "event",
            event: parseChatEvent({ type: "sql", sql: "SELECT 2 AS total", purpose: "Second" }),
        });
        state = chatReducer(state, {
            type: "event",
            event: parseChatEvent({
                type: "table",
                columns: [{ name: "total", type: "number" }],
                rows: [[20]],
                truncated: true,
                elapsed_ms: 9,
            }),
        });
        state = chatReducer(state, {
            type: "event",
            event: parseChatEvent({ type: "chart", kind: "bar", x: "total", y: "total" }),
        });
        expect(state.turns[0]?.queries).toHaveLength(2);
        expect(state.turns[0]?.queries[0]?.sql).toContain("SELECT 1");
        expect(state.turns[0]?.queries[1]?.elapsedMs).toBe(9);
        expect(state.evidence?.rows).toEqual([[20]]);
        expect(state.evidence?.title).toBe("Second");
        expect(state.evidence?.truncated).toBe(true);
    });

    it("shows a refusal on the turn and leaves the previous chart in place", () => {
        let state = start("Where did my money go?");
        state = chatReducer(state, {
            type: "event",
            event: parseChatEvent({
                type: "table",
                columns: [{ name: "total", type: "number" }],
                rows: [[1200]],
                truncated: false,
                elapsed_ms: 3,
            }),
        });
        state = chatReducer(state, {
            type: "event",
            event: parseChatEvent({ type: "chart", kind: "bar", x: "total", y: "total" }),
        });
        state = chatReducer(state, {
            type: "event",
            event: parseChatEvent({
                type: "done",
                message_id: MESSAGE,
                conversation_id: CONVERSATION,
                usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 },
            }),
        });
        const evidence = state.evidence;
        state = chatReducer(state, {
            type: "start",
            localId: "local-2",
            question: "Delete everything",
            createdAt: "2026-10-21T09:00:00.000Z",
        });
        state = chatReducer(state, {
            type: "event",
            event: parseChatEvent({ type: "token", text: "I can only read your statement." }),
        });
        state = chatReducer(state, {
            type: "event",
            event: parseChatEvent({
                type: "done",
                message_id: MESSAGE,
                conversation_id: CONVERSATION,
                usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 },
                status: "refused",
            }),
        });
        expect(state.evidence).toBe(evidence);
        expect(state.turns[1]?.outcome).toBe("refused");
        expect(state.turns[1]?.error?.message).toBe("I can only read your statement.");
        expect(displayedChart(state)).toBe("bar");
    });

    it("records an error without clearing the chart, and keeps clarification options", () => {
        let state = start();
        state = chatReducer(state, {
            type: "event",
            event: parseChatEvent({
                type: "table",
                columns: [{ name: "total", type: "number" }],
                rows: [[5]],
                truncated: false,
            }),
        });
        state = chatReducer(state, {
            type: "event",
            event: parseChatEvent({ type: "chart", kind: "pie", x: "total", y: "total" }),
        });
        const evidence = state.evidence;
        state = chatReducer(state, {
            type: "event",
            event: parseChatEvent({ type: "error", code: "timeout", message: "That took too long." }),
        });
        expect(state.evidence).toBe(evidence);
        expect(state.streaming).toBe(false);
        expect(state.turns[0]?.error?.code).toBe("timeout");

        let clarify = start("Compare something");
        clarify = chatReducer(clarify, {
            type: "event",
            event: parseChatEvent({
                type: "clarification",
                question: "Which period?",
                options: ["Last month", "Last quarter"],
            }),
        });
        expect(clarify.turns[0]?.clarification?.options).toEqual(["Last month", "Last quarter"]);
        expect(clarify.evidence).toBeNull();
    });

    it("restores the conversation on hydrate and drops rows that were not stored", () => {
        let state = start();
        state = chatReducer(state, {
            type: "event",
            event: parseChatEvent({
                type: "table",
                columns: [{ name: "total", type: "number" }],
                rows: [[5]],
                truncated: false,
            }),
        });
        state = chatReducer(state, {
            type: "hydrate",
            conversationId: CONVERSATION,
            messages: [
                {
                    id: MESSAGE,
                    question: "Saved question",
                    sql: "SELECT 1",
                    purpose: "Saved",
                    answer: "Saved answer",
                    status: "answered",
                    createdAt: "2026-10-21T08:00:00.000Z",
                },
            ],
        });
        expect(state.conversationId).toBe(CONVERSATION);
        expect(state.turns[0]?.answer).toBe("Saved answer");
        expect(state.turns[0]?.queries[0]?.sql).toBe("SELECT 1");
        expect(state.evidence).toBeNull();
    });

    it("does not draw a chart when the result is empty or the kind is none", () => {
        let state = start();
        state = chatReducer(state, {
            type: "event",
            event: parseChatEvent({
                type: "table",
                columns: [{ name: "total", type: "number" }],
                rows: [],
                truncated: false,
            }),
        });
        state = chatReducer(state, {
            type: "event",
            event: parseChatEvent({ type: "chart", kind: "bar", x: "total", y: "total" }),
        });
        expect(state.evidence?.rows).toEqual([]);
        expect(displayedChart(state)).toBeNull();

        state = chatReducer(state, {
            type: "event",
            event: parseChatEvent({
                type: "table",
                columns: [{ name: "total", type: "number" }],
                rows: [[1]],
                truncated: false,
            }),
        });
        state = chatReducer(state, {
            type: "event",
            event: parseChatEvent({ type: "chart", kind: "none", x: "", y: "" }),
        });
        expect(displayedChart(state)).toBeNull();
        state = chatReducer(state, { type: "chart", kind: "pie" });
        expect(displayedChart(state)).toBeNull();
    });
});

describe("history groups", () => {
    it("groups chats into today and yesterday in Asia/Kolkata", () => {
        const now = new Date("2026-10-21T12:00:00+05:30");
        const groups = groupConversations(
            [
                {
                    id: CONVERSATION,
                    title: "Today's question",
                    pinned: false,
                    pinned_at: null,
                    created_at: "2026-10-21T04:00:00Z",
                    updated_at: "2026-10-21T05:00:00Z",
                },
                {
                    id: MESSAGE,
                    title: "Yesterday",
                    pinned: false,
                    pinned_at: null,
                    created_at: "2026-10-20T04:00:00Z",
                    updated_at: "2026-10-20T12:00:00Z",
                },
            ],
            now,
        );
        expect(groups.map((group) => group.label)).toEqual(["Today", "Yesterday"]);
    });

    it("keeps pinned chats above the day groups", () => {
        const now = new Date("2026-10-21T12:00:00+05:30");
        const groups = groupConversations(
            [
                {
                    id: CONVERSATION,
                    title: "Loose",
                    pinned: false,
                    pinned_at: null,
                    created_at: "2026-10-21T04:00:00Z",
                    updated_at: "2026-10-21T05:00:00Z",
                },
                {
                    id: MESSAGE,
                    title: "Kept",
                    pinned: true,
                    pinned_at: "2026-10-19T05:00:00Z",
                    created_at: "2026-10-19T04:00:00Z",
                    updated_at: "2026-10-19T05:00:00Z",
                },
            ],
            now,
        );
        expect(groups.map((group) => group.label)).toEqual(["Pinned", "Today"]);
        expect(groups[0]?.items[0]?.title).toBe("Kept");
    });
});
