import type { StatementSummary } from "@/api/eventSchema";
import { Mark, MenuIcon, Wordmark } from "@/components/icons";
import { formatCount, formatRange } from "@/lib/format";
import { useEffect, useRef, useState } from "react";

type TopBarProps = {
    statement: StatementSummary | null;
    statementStatus: "loading" | "ready" | "unavailable";
    name: string;
    email: string;
    onLogout: () => void;
    onOpenHistory: () => void;
};

export function TopBar({ statement, statementStatus, name, email, onLogout, onOpenHistory }: TopBarProps) {
    return (
        <header className="flex h-14 shrink-0 items-center justify-between gap-3 pl-1 pr-6">
            <div className="flex min-w-0 items-center gap-2.5 min-[1120px]:hidden">
                <button
                    type="button"
                    className="grid h-10 w-10 shrink-0 place-items-center rounded-2xl border border-white/10 bg-white/[0.04] text-ink"
                    onClick={onOpenHistory}
                    aria-label="Open chats"
                >
                    <MenuIcon />
                </button>
                <Mark className="h-8 w-8 shrink-0" />
                <p className="truncate text-sm font-semibold">
                    <Wordmark />
                </p>
            </div>
            <div className="ml-auto flex min-w-0 items-center gap-2">
                <StatementMeta statement={statement} status={statementStatus} />
                <UserMenu name={name} email={email} onLogout={onLogout} />
            </div>
        </header>
    );
}

function StatementMeta({
    statement,
    status,
}: {
    statement: StatementSummary | null;
    status: TopBarProps["statementStatus"];
}) {
    if (status === "loading") {
        return <p className="text-xs text-muted">Loading statement…</p>;
    }
    if (status === "unavailable" || !statement) {
        return <p className="text-xs text-muted">Statement summary unavailable</p>;
    }
    return (
        <div className="flex h-11 min-w-0 flex-col justify-center rounded-full border border-white/10 bg-white/[0.04] px-3.5 text-right">
            <p className="truncate text-xs font-semibold sm:text-sm">
                {formatCount(statement.transaction_count)} transactions
            </p>
            <p className="hidden truncate text-[11px] text-muted sm:block">
                {formatRange(statement.covered_from, statement.covered_to)}
            </p>
        </div>
    );
}

function UserMenu({ name, email, onLogout }: { name: string; email: string; onLogout: () => void }) {
    const [open, setOpen] = useState(false);
    const root = useRef<HTMLDivElement>(null);
    const initial = name.slice(0, 1).toUpperCase() || "F";

    useEffect(() => {
        if (!open) return;
        function onPointer(event: PointerEvent) {
            const target = event.target;
            if (!(target instanceof Node) || root.current?.contains(target)) return;
            setOpen(false);
        }
        document.addEventListener("pointerdown", onPointer);
        return () => document.removeEventListener("pointerdown", onPointer);
    }, [open]);

    return (
        <div className="relative" ref={root}>
            <button
                type="button"
                className="flex h-11 items-center gap-2 rounded-full border border-white/10 bg-white/[0.04] pl-1.5 pr-3"
                aria-haspopup="menu"
                aria-expanded={open}
                onClick={() => setOpen((value) => !value)}
            >
                <span className="grid h-8 w-8 place-items-center rounded-full bg-[#2F6BFF] text-sm font-semibold text-white">
                    {initial}
                </span>
                <span className="hidden max-w-[140px] truncate text-sm font-medium sm:block">{name}</span>
            </button>
            {open ? (
                <div role="menu" className="absolute right-0 z-30 mt-2 w-60 rounded-2xl border border-white/10 bg-[#12182c] p-2 shadow-panel">
                    <p className="truncate px-3 py-2 text-xs text-muted">{email}</p>
                    <button
                        type="button"
                        role="menuitem"
                        className="w-full rounded-xl px-3 py-2 text-left text-sm font-medium hover:bg-canvas"
                        onClick={() => {
                            setOpen(false);
                            void onLogout();
                        }}
                    >
                        Sign out
                    </button>
                </div>
            ) : null}
        </div>
    );
}
