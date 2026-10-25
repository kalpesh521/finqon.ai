import { z } from "zod";

import type { components } from "@/types/events";

const cellSchema = z.union([z.string(), z.number(), z.boolean(), z.null()]);

const columnSchema = z.object({
    name: z.string(),
    type: z.string(),
});

const usageSchema = z.object({
    prompt_tokens: z.number(),
    completion_tokens: z.number(),
    total_tokens: z.number(),
});

/** Runtime check for one Server-Sent Event. The reducer only sees the parsed value. */
export const chatEventSchema = z.discriminatedUnion("type", [
    z.object({
        type: z.literal("status"),
        stage: z.enum(["thinking", "querying", "summarizing"]),
    }),
    z.object({
        type: z.literal("sql"),
        sql: z.string(),
        purpose: z.string(),
    }),
    z.object({
        type: z.literal("table"),
        columns: z.array(columnSchema),
        rows: z.array(z.array(cellSchema)),
        truncated: z.boolean(),
        elapsed_ms: z.number().int().nonnegative().optional().default(0),
    }),
    z.object({
        type: z.literal("chart"),
        kind: z.enum(["bar", "pie", "line", "none"]),
        x: z.string(),
        y: z.string(),
    }),
    z.object({
        type: z.literal("clarification"),
        question: z.string(),
        options: z.array(z.string()).max(3),
    }),
    z.object({
        type: z.literal("token"),
        text: z.string(),
    }),
    z.object({
        type: z.literal("done"),
        message_id: z.string().uuid(),
        conversation_id: z.string().uuid(),
        usage: usageSchema,
        status: z.enum(["answered", "clarification", "refused"]).optional().default("answered"),
    }),
    z.object({
        type: z.literal("error"),
        code: z.string(),
        message: z.string(),
    }),
]);

export type ChatEvent = z.infer<typeof chatEventSchema>;
export type Cell = z.infer<typeof cellSchema>;
export type Column = z.infer<typeof columnSchema>;
export type ChartKind = Extract<ChatEvent, { type: "chart" }>["kind"];
export type Stage = Extract<ChatEvent, { type: "status" }>["stage"];

export const tokenPairSchema = z.object({
    access_token: z.string().min(1),
    refresh_token: z.string().min(1),
    token_type: z.string(),
    expires_in: z.number().positive(),
});

export const userSchema = z.object({
    id: z.string().uuid(),
    email: z.string().email(),
    display_name: z.string().nullable().optional(),
});

export const statementSchema = z.object({
    transaction_count: z.number().int().nonnegative(),
    covered_from: z.string().nullable(),
    covered_to: z.string().nullable(),
});

export const conversationSummarySchema = z.object({
    id: z.string().uuid(),
    title: z.string(),
    pinned: z.boolean().default(false),
    pinned_at: z.string().nullable().default(null),
    created_at: z.string(),
    updated_at: z.string(),
});

export const messageRecordSchema = z.object({
    id: z.string().uuid(),
    question: z.string(),
    sql: z.string().nullable(),
    purpose: z.string().nullable(),
    result_summary: z.string().nullable(),
    answer: z.string(),
    status: z.enum(["answered", "clarification", "refused", "error"]),
    prompt_tokens: z.number(),
    completion_tokens: z.number(),
    created_at: z.string(),
});

export const conversationDetailSchema = conversationSummarySchema.extend({
    messages: z.array(messageRecordSchema),
});

export type TokenPair = z.infer<typeof tokenPairSchema>;
export type UserResponse = z.infer<typeof userSchema>;
export const fileRecordSchema = z.object({
    id: z.string().uuid(),
    filename: z.string(),
    size_bytes: z.number().int().nonnegative(),
    status: z.enum(["queued", "processing", "ready", "failed", "quarantined"]),
    stage: z.string(),
    progress: z.number().int().min(0).max(100),
    rows_imported: z.number().int().nonnegative(),
    rows_skipped: z.number().int().nonnegative(),
    date_from: z.string().nullable(),
    date_to: z.string().nullable(),
    warnings: z.array(z.string()),
    error: z.string().nullable(),
    created_at: z.string(),
    completed_at: z.string().nullable(),
});

export type FileRecord = z.infer<typeof fileRecordSchema>;
export type StatementSummary = z.infer<typeof statementSchema>;
export type ConversationSummary = z.infer<typeof conversationSummarySchema>;
export type MessageRecord = z.infer<typeof messageRecordSchema>;
export type ConversationDetail = z.infer<typeof conversationDetailSchema>;

export function parseChatEvent(value: unknown): ChatEvent {
    return chatEventSchema.parse(value);
}

type GeneratedEvent = components["schemas"]["ChatEvent"];
type _EventMatchesApi = ChatEvent extends GeneratedEvent ? true : never;
const eventMatchesApi: _EventMatchesApi = true;
void eventMatchesApi;
