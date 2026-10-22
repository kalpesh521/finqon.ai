import type { ConversationSummary } from "@/api/eventSchema";

export type HistoryGroup = {
    label: string;
    items: ConversationSummary[];
};

const TIME_ZONE = "Asia/Kolkata";

export function kolkataDayKey(instant: Date): string {
    return new Intl.DateTimeFormat("en-CA", {
        timeZone: TIME_ZONE,
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
    }).format(instant);
}

export function groupConversations(
    conversations: ConversationSummary[],
    now = new Date(),
): HistoryGroup[] {
    const pinned = conversations
        .filter((conversation) => conversation.pinned)
        .sort((left, right) => (right.pinned_at ?? "").localeCompare(left.pinned_at ?? ""));
    const today = kolkataDayKey(now);
    const yesterday = kolkataDayKey(new Date(now.getTime() - 86_400_000));
    const sorted = conversations
        .filter((conversation) => !conversation.pinned)
        .sort((left, right) => right.updated_at.localeCompare(left.updated_at));
    const order: string[] = [];
    const buckets = new Map<string, ConversationSummary[]>();
    for (const conversation of sorted) {
        const key = kolkataDayKey(new Date(conversation.updated_at));
        const existing = buckets.get(key);
        if (existing) {
            existing.push(conversation);
        } else {
            buckets.set(key, [conversation]);
            order.push(key);
        }
    }
    const dated = order.map((key) => ({
        label: key === today ? "Today" : key === yesterday ? "Yesterday" : formatGroupDay(key),
        items: buckets.get(key) ?? [],
    }));
    return pinned.length > 0 ? [{ label: "Pinned", items: pinned }, ...dated] : dated;
}

function formatGroupDay(key: string): string {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(key);
    if (!match) return key;
    const year = Number(match[1]);
    const month = Number(match[2]);
    const day = Number(match[3]);
    if (!year || !month || !day) return key;
    return new Intl.DateTimeFormat("en-IN", {
        day: "numeric",
        month: "short",
        year: "numeric",
        timeZone: "UTC",
    }).format(new Date(Date.UTC(year, month - 1, day)));
}

export const FOLLOW_UPS = [
    "Compare with the previous month",
    "Break this down by merchant",
    "Who are my top 5 merchants?",
] as const;

export const STARTERS = [
    "How much did I spend on dining last month?",
    "Show my spending by category",
    "Which merchant received the most money?",
] as const;
