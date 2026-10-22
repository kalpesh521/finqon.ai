const AUTH_KEY = "finqon.auth";
const CONVERSATION_KEY = "finqon.conversation";

export type StoredAuth = {
    refreshToken: string;
    persistent: boolean;
};

export function readStoredAuth(): StoredAuth | null {
    return parseStored(window.localStorage.getItem(AUTH_KEY)) ??
        parseStored(window.sessionStorage.getItem(AUTH_KEY));
}

export function writeStoredAuth(value: StoredAuth): void {
    window.localStorage.removeItem(AUTH_KEY);
    window.sessionStorage.removeItem(AUTH_KEY);
    const target = value.persistent ? window.localStorage : window.sessionStorage;
    target.setItem(AUTH_KEY, JSON.stringify(value));
}

export function clearStoredAuth(): void {
    window.localStorage.removeItem(AUTH_KEY);
    window.sessionStorage.removeItem(AUTH_KEY);
    window.localStorage.removeItem(CONVERSATION_KEY);
    window.sessionStorage.removeItem(CONVERSATION_KEY);
}

export function readConversationId(): string | null {
    return (
        window.sessionStorage.getItem(CONVERSATION_KEY) ??
        window.localStorage.getItem(CONVERSATION_KEY)
    );
}

export function writeConversationId(conversationId: string | null, persistent: boolean): void {
    window.localStorage.removeItem(CONVERSATION_KEY);
    window.sessionStorage.removeItem(CONVERSATION_KEY);
    if (!conversationId) return;
    const target = persistent ? window.localStorage : window.sessionStorage;
    target.setItem(CONVERSATION_KEY, conversationId);
}

function parseStored(raw: string | null): StoredAuth | null {
    if (!raw) return null;
    try {
        const value: unknown = JSON.parse(raw);
        if (
            typeof value === "object" &&
            value !== null &&
            "refreshToken" in value &&
            "persistent" in value &&
            typeof value.refreshToken === "string" &&
            value.refreshToken.length > 0 &&
            typeof value.persistent === "boolean"
        ) {
            return { refreshToken: value.refreshToken, persistent: value.persistent };
        }
    } catch {
        return null;
    }
    return null;
}
