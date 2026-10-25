import { z } from "zod";

import {
    conversationDetailSchema,
    fileRecordSchema,
    conversationSummarySchema,
    statementSchema,
    tokenPairSchema,
    userSchema,
    type ConversationDetail,
    type ConversationSummary,
    type FileRecord,
    type StatementSummary,
    type TokenPair,
    type UserResponse,
} from "@/api/eventSchema";

const API_BASE = apiBase();

export class ApiError extends Error {
    readonly status: number;
    readonly code: string;

    constructor(status: number, code: string, message: string) {
        super(message);
        this.name = "ApiError";
        this.status = status;
        this.code = code;
    }
}

export type AuthorizedFetch = (path: string, init?: RequestInit) => Promise<Response>;

function apiBase(): string {
    const value: unknown = import.meta.env.VITE_API_BASE;
    if (typeof value !== "string") return "";
    return value.replace(/\/$/, "");
}

export async function loginRequest(email: string, password: string): Promise<TokenPair> {
    return sendJson("/api/v1/auth/login", { email, password }, tokenPairSchema);
}

export async function registerRequest(
    displayName: string,
    email: string,
    password: string,
): Promise<void> {
    const response = await fetch(`${API_BASE}/api/v1/auth/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ display_name: displayName, email, password }),
        signal: AbortSignal.timeout(20_000),
    });
    if (!response.ok) throw await readError(response);
}

export async function refreshRequest(refreshToken: string): Promise<TokenPair> {
    return sendJson("/api/v1/auth/refresh", { refresh_token: refreshToken }, tokenPairSchema);
}

export async function logoutRequest(refreshToken: string): Promise<void> {
    const response = await fetch(`${API_BASE}/api/v1/auth/logout`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (!response.ok && response.status !== 204) throw await readError(response);
}

export async function currentUser(accessToken: string): Promise<UserResponse> {
    const response = await fetch(`${API_BASE}/api/v1/auth/me`, {
        headers: { Authorization: `Bearer ${accessToken}` },
        signal: AbortSignal.timeout(20_000),
    });
    if (!response.ok) throw await readError(response);
    return userSchema.parse(await response.json());
}

export async function listConversations(fetchAuth: AuthorizedFetch): Promise<ConversationSummary[]> {
    const response = await fetchAuth("/api/v1/conversations?limit=50");
    if (!response.ok) throw await readError(response);
    return z.array(conversationSummarySchema).parse(await response.json());
}

export async function getConversation(
    fetchAuth: AuthorizedFetch,
    conversationId: string,
): Promise<ConversationDetail> {
    const response = await fetchAuth(`/api/v1/conversations/${conversationId}`);
    if (!response.ok) throw await readError(response);
    return conversationDetailSchema.parse(await response.json());
}

export async function updateConversation(
    fetchAuth: AuthorizedFetch,
    conversationId: string,
    change: { title?: string; pinned?: boolean },
): Promise<ConversationSummary> {
    const response = await fetchAuth(`/api/v1/conversations/${conversationId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(change),
    });
    if (!response.ok) throw await readError(response);
    return conversationSummarySchema.parse(await response.json());
}

export async function deleteConversation(
    fetchAuth: AuthorizedFetch,
    conversationId: string,
): Promise<void> {
    const response = await fetchAuth(`/api/v1/conversations/${conversationId}`, { method: "DELETE" });
    if (!response.ok && response.status !== 204) throw await readError(response);
}

export async function getStatement(fetchAuth: AuthorizedFetch): Promise<StatementSummary> {
    const response = await fetchAuth("/api/v1/statement");
    if (!response.ok) throw await readError(response);
    return statementSchema.parse(await response.json());
}

export async function uploadFile(fetchAuth: AuthorizedFetch, file: File): Promise<FileRecord> {
    const body = new FormData();
    body.append("files", file, file.name);
    const response = await fetchAuth("/api/v1/files", { method: "POST", body });
    if (!response.ok) throw await readError(response);
    const records = z.array(fileRecordSchema).parse(await response.json());
    const first = records[0];
    if (!first) throw new ApiError(502, "http_error", "The upload was not accepted.");
    return first;
}

export async function listFiles(fetchAuth: AuthorizedFetch): Promise<FileRecord[]> {
    const response = await fetchAuth("/api/v1/files");
    if (!response.ok) throw await readError(response);
    return z.array(fileRecordSchema).parse(await response.json());
}

export async function getFile(fetchAuth: AuthorizedFetch, id: string): Promise<FileRecord> {
    const response = await fetchAuth(`/api/v1/files/${id}`);
    if (!response.ok) throw await readError(response);
    return fileRecordSchema.parse(await response.json());
}

export async function deleteFile(fetchAuth: AuthorizedFetch, id: string): Promise<void> {
    const response = await fetchAuth(`/api/v1/files/${id}`, { method: "DELETE" });
    if (!response.ok && response.status !== 204) throw await readError(response);
}

export function apiUrl(path: string): string {
    return `${API_BASE}${path}`;
}

async function sendJson<T>(path: string, body: unknown, schema: z.ZodType<T>): Promise<T> {
    const response = await fetch(`${API_BASE}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(20_000),
    });
    if (!response.ok) throw await readError(response);
    return schema.parse(await response.json());
}

export async function readError(response: Response): Promise<ApiError> {
    let payload: unknown = null;
    try {
        payload = await response.json();
    } catch {
        payload = null;
    }
    if (isRecord(payload) && typeof payload.message === "string") {
        const code = typeof payload.code === "string" ? payload.code : "http_error";
        return new ApiError(response.status, code, payload.message);
    }
    if (isRecord(payload) && Array.isArray(payload.detail)) {
        const first = payload.detail[0];
        if (isRecord(first) && typeof first.msg === "string") {
            return new ApiError(response.status, "invalid_request", first.msg);
        }
    }
    return new ApiError(response.status, "http_error", "The request could not be completed.");
}

function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === "object" && value !== null && !Array.isArray(value);
}
