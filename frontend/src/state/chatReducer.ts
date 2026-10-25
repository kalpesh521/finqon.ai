import type { Cell, ChartKind, ChatEvent, Column, Stage } from "@/api/eventSchema";

export type ChartSpec = {
    kind: ChartKind;
    x: string;
    y: string;
};

export type QueryView = {
    sql: string;
    purpose: string;
    elapsedMs: number | null;
    columns: Column[];
    rows: Cell[][];
    truncated: boolean;
    chart: ChartSpec | null;
    settled: boolean;
};

export type TurnOutcome = "pending" | "answered" | "clarification" | "refused" | "error" | "info";

export type Turn = {
    localId: string;
    messageId: string | null;
    question: string;
    answer: string;
    stage: Stage | null;
    queries: QueryView[];
    clarification: { question: string; options: string[] } | null;
    error: { code: string; message: string } | null;
    outcome: TurnOutcome;
    createdAt: string;
};

export type Evidence = {
    title: string;
    columns: Column[];
    rows: Cell[][];
    truncated: boolean;
    chart: ChartSpec;
};

export type HistoryMessage = {
    id: string;
    question: string;
    sql: string | null;
    purpose: string | null;
    answer: string;
    status: "answered" | "clarification" | "refused" | "error" | "info";
    createdAt: string;
};

export type ChatState = {
    conversationId: string | null;
    turns: Turn[];
    evidence: Evidence | null;
    chartSelection: "bar" | "pie" | "line" | null;
    streaming: boolean;
};

export type ChatAction =
    | { type: "hydrate"; conversationId: string; messages: HistoryMessage[] }
    | { type: "new_chat" }
    | { type: "start"; localId: string; question: string; createdAt: string }
    | { type: "conversation"; conversationId: string }
    | { type: "event"; event: ChatEvent }
    | { type: "transport_error"; message: string }
    | { type: "chart"; kind: "bar" | "pie" | "line" };

export const initialChatState: ChatState = {
    conversationId: null,
    turns: [],
    evidence: null,
    chartSelection: null,
    streaming: false,
};

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
    switch (action.type) {
        case "hydrate":
            return {
                ...state,
                conversationId: action.conversationId,
                turns: action.messages.map(turnFromHistory),
                evidence: null,
                chartSelection: null,
                streaming: false,
            };
        case "new_chat":
            return { ...initialChatState };
        case "start":
            if (state.streaming) return state;
            return {
                ...state,
                streaming: true,
                turns: [
                    ...state.turns,
                    {
                        localId: action.localId,
                        messageId: null,
                        question: action.question,
                        answer: "",
                        stage: "thinking",
                        queries: [],
                        clarification: null,
                        error: null,
                        outcome: "pending",
                        createdAt: action.createdAt,
                    },
                ],
            };
        case "conversation":
            return { ...state, conversationId: action.conversationId };
        case "event":
            return applyEvent(state, action.event);
        case "transport_error":
            return failTurn(state, "transport", action.message);
        case "chart":
            if (!state.evidence || state.evidence.chart.kind === "none") return state;
            if (state.evidence.rows.length === 0) return state;
            return { ...state, chartSelection: action.kind };
        default:
            return assertNever(action);
    }
}

function applyEvent(state: ChatState, event: ChatEvent): ChatState {
    switch (event.type) {
        case "status":
            return mapLast(state, (turn) => ({ ...turn, stage: event.stage }));
        case "sql":
            return mapLast(state, (turn) => ({
                ...turn,
                queries: [
                    ...turn.queries,
                    {
                        sql: event.sql,
                        purpose: event.purpose,
                        elapsedMs: null,
                        columns: [],
                        rows: [],
                        truncated: false,
                        chart: null,
                        settled: false,
                    },
                ],
            }));
        case "table":
            return applyTable(state, event);
        case "chart":
            return applyChart(state, event);
        case "clarification":
            return mapLast(state, (turn) => ({
                ...turn,
                clarification: { question: event.question, options: event.options },
            }));
        case "token":
            return mapLast(state, (turn) => ({ ...turn, answer: turn.answer + event.text }));
        case "retract":
            // Live text failed the grounding check (or preceded a tool call); a verified
            // replacement follows.
            return mapLast(state, (turn) => ({ ...turn, answer: "" }));
        case "done":
            return mapLast(
                { ...state, conversationId: event.conversation_id, streaming: false },
                (turn) => {
                    if (event.status === "refused") {
                        return {
                            ...turn,
                            stage: null,
                            outcome: "refused",
                            messageId: event.message_id,
                            error: {
                                code: "refused",
                                message:
                                    turn.answer ||
                                    "I can't answer that from your statement.",
                            },
                            answer: "",
                        };
                    }
                    return {
                        ...turn,
                        stage: null,
                        outcome: event.status,
                        messageId: event.message_id,
                    };
                },
            );
        case "error":
            return failTurn(state, event.code, event.message);
        default:
            return assertNever(event);
    }
}

function applyTable(state: ChatState, event: Extract<ChatEvent, { type: "table" }>): ChatState {
    const next = mapLast(state, (turn) => {
        const queries = turn.queries.map((query) => ({ ...query }));
        const index = lastUnsettled(queries);
        const current = index >= 0 ? queries[index] : undefined;
        const settled: QueryView = {
            sql: current?.sql ?? "",
            purpose: current?.purpose ?? "",
            elapsedMs: event.elapsed_ms,
            columns: event.columns,
            rows: event.rows,
            truncated: event.truncated,
            chart: current?.chart ?? null,
            settled: true,
        };
        if (index >= 0) queries[index] = settled;
        else queries.push(settled);
        return { ...turn, queries };
    });
    const turn = next.turns[next.turns.length - 1];
    const settledQuery = lastSettled(turn?.queries ?? []);
    return {
        ...next,
        chartSelection: null,
        evidence: {
            title: settledQuery?.purpose || "Result",
            columns: event.columns,
            rows: event.rows,
            truncated: event.truncated,
            chart: settledQuery?.chart ?? { kind: "none", x: "", y: "" },
        },
    };
}

function applyChart(state: ChatState, event: Extract<ChatEvent, { type: "chart" }>): ChatState {
    const spec: ChartSpec = { kind: event.kind, x: event.x, y: event.y };
    let updateEvidence = false;
    const next = mapLast(state, (turn) => {
        if (turn.queries.length === 0) return turn;
        const queries = turn.queries.map((query) => ({ ...query }));
        const index = queries.length - 1;
        const last = queries[index];
        if (!last) return turn;
        updateEvidence = last.settled;
        queries[index] = { ...last, chart: spec };
        return { ...turn, queries };
    });
    if (!next.evidence || !updateEvidence) return next;
    return { ...next, evidence: { ...next.evidence, chart: spec } };
}

function failTurn(state: ChatState, code: string, message: string): ChatState {
    return mapLast({ ...state, streaming: false }, (turn) => ({
        ...turn,
        stage: null,
        outcome: "error",
        error: { code, message },
    }));
}

function mapLast(state: ChatState, update: (turn: Turn) => Turn): ChatState {
    const turn = state.turns[state.turns.length - 1];
    if (!turn) return state;
    return { ...state, turns: [...state.turns.slice(0, -1), update(turn)] };
}

function lastSettled(queries: QueryView[]): QueryView | undefined {
    for (let index = queries.length - 1; index >= 0; index -= 1) {
        const query = queries[index];
        if (query?.settled) return query;
    }
    return undefined;
}

function assertNever(value: never): never {
    throw new Error(`Unhandled chat action: ${JSON.stringify(value)}`);
}

function lastUnsettled(queries: QueryView[]): number {
    for (let index = queries.length - 1; index >= 0; index -= 1) {
        const query = queries[index];
        if (query && !query.settled) return index;
    }
    return -1;
}

function turnFromHistory(message: HistoryMessage): Turn {
    const failed = message.status === "refused" || message.status === "error";
    return {
        localId: message.id,
        messageId: message.id,
        question: message.question,
        answer: failed ? "" : message.status === "clarification" ? "" : message.answer,
        stage: null,
        queries: message.sql
            ? [
                  {
                      sql: message.sql,
                      purpose: message.purpose ?? "",
                      elapsedMs: null,
                      columns: [],
                      rows: [],
                      truncated: false,
                      chart: null,
                      settled: false,
                  },
              ]
            : [],
        clarification:
            message.status === "clarification"
                ? { question: message.answer, options: [] }
                : null,
        error: failed ? { code: message.status, message: message.answer } : null,
        outcome: message.status,
        createdAt: message.createdAt,
    };
}

export function displayedChart(state: ChatState): "bar" | "pie" | "line" | null {
    const evidence = state.evidence;
    if (!evidence || evidence.rows.length === 0 || evidence.chart.kind === "none") return null;
    return state.chartSelection ?? evidence.chart.kind;
}
