import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import { readChatStream } from "@/api/chatStream";
import type { ConversationSummary, MessageRecord, StatementSummary } from "@/api/eventSchema";
import {
    ApiError,
    deleteConversation,
    getConversation,
    getStatement,
    listConversations,
    readError,
    updateConversation,
} from "@/api/http";
import { useAuth } from "@/auth/AuthProvider";
import { readConversationId, writeConversationId } from "@/auth/storage";
import type { HistoryMessage } from "@/state/chatReducer";
import { chatReducer, initialChatState } from "@/state/chatReducer";

export type ChartChoice = "bar" | "pie" | "line";

function toHistory(message: MessageRecord): HistoryMessage {
    return {
        id: message.id,
        question: message.question,
        sql: message.sql,
        purpose: message.purpose,
        answer: message.answer,
        status: message.status,
        createdAt: message.created_at,
    };
}

function messageOf(error: unknown): string {
    if (error instanceof ApiError) return error.message;
    if (error instanceof Error) return error.message;
    return "Something went wrong.";
}

export function useChatStream() {
    const { authorizedFetch, persistent } = useAuth();
    const [state, dispatch] = useReducer(chatReducer, initialChatState);
    const [conversations, setConversations] = useState<ConversationSummary[]>([]);
    const [historyError, setHistoryError] = useState<string | null>(null);
    const [statement, setStatement] = useState<StatementSummary | null>(null);
    const [statementStatus, setStatementStatus] = useState<"loading" | "ready" | "unavailable">(
        "loading",
    );
    const stateRef = useRef(state);
    const sendingRef = useRef(false);
    const abortRef = useRef<AbortController | null>(null);
    const savedAtStart = useRef(readConversationId());
    stateRef.current = state;

    useEffect(() => {
        if (state.conversationId) {
            writeConversationId(state.conversationId, persistent);
            savedAtStart.current = null;
            return;
        }
        // Mount starts with no id in memory. Keep the stored one until restore or an explicit new chat.
        if (savedAtStart.current) return;
        writeConversationId(null, persistent);
    }, [state.conversationId, persistent]);

    useEffect(() => () => abortRef.current?.abort(), []);

    const reloadHistory = useCallback(async () => {
        const list = await listConversations(authorizedFetch);
        setConversations(list);
    }, [authorizedFetch]);

    const refreshStatement = useCallback(() => {
        void getStatement(authorizedFetch)
            .then((summary) => {
                setStatement(summary);
                setStatementStatus("ready");
            })
            .catch(() => {
                setStatementStatus((current) => (current === "ready" ? current : "unavailable"));
            });
    }, [authorizedFetch]);

    useEffect(() => {
        refreshStatement();
    }, [refreshStatement]);

    useEffect(() => {
        const saved = savedAtStart.current;
        let cancelled = false;
        void (async () => {
            try {
                const list = await listConversations(authorizedFetch);
                if (cancelled) return;
                setConversations(list);
                if (!saved || !list.some((item) => item.id === saved)) return;
                const detail = await getConversation(authorizedFetch, saved);
                if (!cancelled) {
                    dispatch({
                        type: "hydrate",
                        conversationId: detail.id,
                        messages: detail.messages.map(toHistory),
                    });
                }
            } catch (error) {
                if (!cancelled) setHistoryError(messageOf(error));
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [authorizedFetch]);

    const send = useCallback(
        async (question: string) => {
            const text = question.trim();
            if (!text || sendingRef.current || stateRef.current.streaming) return;
            sendingRef.current = true;
            const conversationId = stateRef.current.conversationId;
            dispatch({
                type: "start",
                localId: crypto.randomUUID(),
                question: text,
                createdAt: new Date().toISOString(),
            });
            const controller = new AbortController();
            abortRef.current = controller;
            try {
                const response = await authorizedFetch("/api/v1/chat", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        Accept: "text/event-stream",
                    },
                    body: JSON.stringify({ message: text, conversation_id: conversationId }),
                    signal: controller.signal,
                });
                if (!response.ok) {
                    dispatch({ type: "transport_error", message: (await readError(response)).message });
                    return;
                }
                const headerId = response.headers.get("X-Conversation-Id");
                if (headerId) dispatch({ type: "conversation", conversationId: headerId });
                await readChatStream(response, (event) => dispatch({ type: "event", event }));
                try {
                    await reloadHistory();
                } catch {
                    // The answer is already visible. History can refresh on the next question.
                }
            } catch (error) {
                if (controller.signal.aborted) return;
                dispatch({ type: "transport_error", message: messageOf(error) });
            } finally {
                sendingRef.current = false;
            }
        },
        [authorizedFetch, reloadHistory],
    );

    const openConversation = useCallback(
        async (conversationId: string) => {
            if (stateRef.current.streaming || conversationId === stateRef.current.conversationId) {
                return;
            }
            try {
                const detail = await getConversation(authorizedFetch, conversationId);
                dispatch({
                    type: "hydrate",
                    conversationId: detail.id,
                    messages: detail.messages.map(toHistory),
                });
                setHistoryError(null);
            } catch (error) {
                setHistoryError(messageOf(error));
            }
        },
        [authorizedFetch],
    );

    const newChat = useCallback(() => {
        if (stateRef.current.streaming) return;
        dispatch({ type: "new_chat" });
    }, []);

    const renameConversation = useCallback(
        async (conversationId: string, title: string) => {
            try {
                const updated = await updateConversation(authorizedFetch, conversationId, { title });
                setConversations((current) =>
                    current.map((item) => (item.id === updated.id ? updated : item)),
                );
                setHistoryError(null);
            } catch (error) {
                setHistoryError(messageOf(error));
            }
        },
        [authorizedFetch],
    );

    const setPinned = useCallback(
        async (conversationId: string, pinned: boolean) => {
            try {
                const updated = await updateConversation(authorizedFetch, conversationId, { pinned });
                setConversations((current) =>
                    current.map((item) => (item.id === updated.id ? updated : item)),
                );
                setHistoryError(null);
            } catch (error) {
                setHistoryError(messageOf(error));
            }
        },
        [authorizedFetch],
    );

    const removeConversation = useCallback(
        async (conversationId: string) => {
            if (stateRef.current.streaming && stateRef.current.conversationId === conversationId) {
                setHistoryError("Wait for the answer to finish before deleting this chat.");
                return;
            }
            try {
                await deleteConversation(authorizedFetch, conversationId);
                setConversations((current) => current.filter((item) => item.id !== conversationId));
                if (stateRef.current.conversationId === conversationId) dispatch({ type: "new_chat" });
                setHistoryError(null);
            } catch (error) {
                setHistoryError(messageOf(error));
            }
        },
        [authorizedFetch],
    );

    const setChart = useCallback((kind: ChartChoice) => {
        dispatch({ type: "chart", kind });
    }, []);

    return {
        state,
        conversations,
        historyError,
        statement,
        statementStatus,
        refreshStatement,
        send,
        openConversation,
        newChat,
        renameConversation,
        setPinned,
        removeConversation,
        setChart,
    };
}
