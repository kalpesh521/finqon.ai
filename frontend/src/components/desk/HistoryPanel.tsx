import { useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";

import type { ConversationSummary } from "@/api/eventSchema";
import { EllipsisIcon, Mark, PanelIcon, PencilIcon, PinIcon, PlusIcon, TrashIcon, Wordmark } from "@/components/icons";
import { cx } from "@/lib/cx";
import { groupConversations } from "@/lib/history";

type HistoryPanelProps = {
    conversations: ConversationSummary[];
    activeId: string | null;
    error: string | null;
    collapsed?: boolean;
    onToggle?: () => void;
    toggleLabel?: string;
    onOpen: (id: string) => void;
    onNew: () => void;
    onRename: (id: string, title: string) => void;
    onPin: (id: string, pinned: boolean) => void;
    onDelete: (id: string) => void;
};

export function HistoryPanel({
    conversations,
    activeId,
    error,
    collapsed = false,
    onToggle,
    toggleLabel = "Collapse sidebar",
    onOpen,
    onNew,
    onRename,
    onPin,
    onDelete,
}: HistoryPanelProps) {
    const groups = groupConversations(conversations);
    const [menuId, setMenuId] = useState<string | null>(null);
    const [renamingId, setRenamingId] = useState<string | null>(null);

    if (collapsed) {
        return (
            <div className="flex h-full min-h-0 flex-col items-center rounded-3xl border border-white/10 bg-[#0A0E18]/90 py-3 shadow-panel backdrop-blur-xl">
                {onToggle ? (
                    <button
                        type="button"
                        onClick={onToggle}
                        aria-label="Expand sidebar"
                        aria-expanded={false}
                        className="grid h-9 w-9 place-items-center rounded-xl border border-white/10 bg-white/[0.04] text-white/70 transition hover:bg-white/10 hover:text-white"
                    >
                        <PanelIcon collapsed />
                    </button>
                ) : null}
                <Mark className="mt-4 h-8 w-8" />
                <button
                    type="button"
                    onClick={onNew}
                    aria-label="New chat"
                    className="mt-4 grid h-9 w-9 place-items-center rounded-xl bg-[#2F6BFF]/20 text-[#b9d0ff] transition hover:bg-[#2F6BFF]/30"
                >
                    <PlusIcon />
                </button>
            </div>
        );
    }

    return (
        <div className="flex h-full min-h-0 flex-col overflow-hidden rounded-3xl border border-white/10 bg-[#0A0E18]/90 shadow-panel backdrop-blur-xl">
            <div className="px-3.5 pt-3.5">
                <div className="flex items-center justify-between gap-2">
                    <div className="flex min-w-0 items-center gap-2">
                        <Mark className="h-8 w-8 shrink-0" />
                        <p className="truncate text-[15px] font-semibold leading-none">
                            <Wordmark />
                        </p>
                    </div>
                    {onToggle ? (
                        <button
                            type="button"
                            onClick={onToggle}
                            aria-label={toggleLabel}
                            aria-expanded
                            className="grid h-8 w-8 shrink-0 place-items-center rounded-xl border border-white/10 bg-white/[0.04] text-white/70 transition hover:bg-white/10 hover:text-white"
                        >
                            <PanelIcon />
                        </button>
                    ) : null}
                </div>
                <p className="mt-2 text-[10px] font-semibold uppercase leading-4 tracking-[0.14em] text-muted">
                    AI Finance Assistant
                </p>
            </div>
            <div className="px-3 pt-4">
                <button
                    type="button"
                    onClick={onNew}
                    className="flex h-10 w-full items-center justify-center gap-2 rounded-2xl bg-[#2F6BFF]/18 text-sm font-semibold text-[#c5d8ff] transition hover:bg-[#2F6BFF]/28"
                >
                    <PlusIcon />
                    New chat
                </button>
            </div>
            <div className="no-scrollbar mt-3 min-h-0 flex-1 overflow-y-auto px-2 pb-3">
                {error ? <p className="px-2 py-2 text-sm text-[#be123c]">{error}</p> : null}
                {groups.length === 0 ? (
                    <p className="px-2 py-6 text-sm text-muted">No questions yet.</p>
                ) : (
                    groups.map((group) => (
                        <section key={group.label} className="mb-4">
                            <h2 className="px-2 pb-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-muted">
                                {group.label}
                            </h2>
                            <ul>
                                {group.items.map((item) => (
                                    <ChatRow
                                        key={item.id}
                                        item={item}
                                        active={item.id === activeId}
                                        menuOpen={menuId === item.id}
                                        renaming={renamingId === item.id}
                                        onOpen={() => {
                                            setMenuId(null);
                                            onOpen(item.id);
                                        }}
                                        onToggleMenu={() => setMenuId((current) => (current === item.id ? null : item.id))}
                                        onCloseMenu={() => setMenuId(null)}
                                        onStartRename={() => {
                                            setMenuId(null);
                                            setRenamingId(item.id);
                                        }}
                                        onCancelRename={() => setRenamingId(null)}
                                        onRename={(title) => {
                                            setRenamingId(null);
                                            if (title !== item.title) onRename(item.id, title);
                                        }}
                                        onPin={() => {
                                            setMenuId(null);
                                            onPin(item.id, !item.pinned);
                                        }}
                                        onDelete={() => {
                                            setMenuId(null);
                                            onDelete(item.id);
                                        }}
                                    />
                                ))}
                            </ul>
                        </section>
                    ))
                )}
            </div>
        </div>
    );
}

type ChatRowProps = {
    item: ConversationSummary;
    active: boolean;
    menuOpen: boolean;
    renaming: boolean;
    onOpen: () => void;
    onToggleMenu: () => void;
    onCloseMenu: () => void;
    onStartRename: () => void;
    onCancelRename: () => void;
    onRename: (title: string) => void;
    onPin: () => void;
    onDelete: () => void;
};

function ChatRow({
    item,
    active,
    menuOpen,
    renaming,
    onOpen,
    onToggleMenu,
    onCloseMenu,
    onStartRename,
    onCancelRename,
    onRename,
    onPin,
    onDelete,
}: ChatRowProps) {
    const menuButton = useRef<HTMLButtonElement>(null);
    const renameClosed = useRef(false);
    const inputId = useId();

    function finishRename(save: boolean, value: string) {
        if (renameClosed.current) return;
        renameClosed.current = true;
        const title = value.trim().replace(/\s+/g, " ");
        if (!save || !title) {
            onCancelRename();
            return;
        }
        onRename(title.slice(0, 120));
    }

    return (
        <li className="relative">
            <div
                className={cx(
                    "flex items-center rounded-2xl",
                    active ? "bg-accent-soft" : "hover:bg-white/5",
                )}
            >
                {renaming ? (
                    <input
                        id={inputId}
                        aria-label="Chat name"
                        defaultValue={item.title}
                        autoFocus
                        maxLength={120}
                        className="m-1 h-9 min-w-0 flex-1 rounded-xl border border-accent bg-[#0d1424] px-2 text-sm text-white outline-none"
                        onKeyDown={(event: KeyboardEvent<HTMLInputElement>) => {
                            if (event.key === "Enter") finishRename(true, event.currentTarget.value);
                            if (event.key === "Escape") finishRename(false, event.currentTarget.value);
                        }}
                        onBlur={(event) => finishRename(true, event.currentTarget.value)}
                    />
                ) : (
                    <button
                        type="button"
                        onClick={onOpen}
                        aria-current={active ? "true" : undefined}
                        className={cx(
                            "min-w-0 flex-1 rounded-2xl px-3 py-2.5 text-left text-sm",
                            active ? "font-semibold text-accent" : "text-ink",
                        )}
                    >
                        <span className="flex items-start gap-1.5">
                            {item.pinned ? <PinIcon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-accent" /> : null}
                            <span className="line-clamp-2">{item.title}</span>
                        </span>
                    </button>
                )}
                {renaming ? null : (
                    <button
                        ref={menuButton}
                        type="button"
                        aria-label={`Actions for ${item.title}`}
                        aria-haspopup="menu"
                        aria-expanded={menuOpen}
                        onClick={onToggleMenu}
                        className="mr-1 grid h-8 w-8 shrink-0 place-items-center rounded-xl text-muted hover:bg-white/10 hover:text-white"
                    >
                        <EllipsisIcon />
                    </button>
                )}
            </div>
            {menuOpen ? (
                <ChatMenu
                    anchor={menuButton.current}
                    pinned={item.pinned}
                    onClose={onCloseMenu}
                    onRename={onStartRename}
                    onPin={onPin}
                    onDelete={onDelete}
                />
            ) : null}
        </li>
    );
}

function ChatMenu({
    anchor,
    pinned,
    onClose,
    onRename,
    onPin,
    onDelete,
}: {
    anchor: HTMLButtonElement | null;
    pinned: boolean;
    onClose: () => void;
    onRename: () => void;
    onPin: () => void;
    onDelete: () => void;
}) {
    const menu = useRef<HTMLDivElement>(null);
    const [confirming, setConfirming] = useState(false);
    const [place, setPlace] = useState<{ top: number; left: number } | null>(null);

    useEffect(() => {
        if (!anchor) return;
        const rect = anchor.getBoundingClientRect();
        const width = 188;
        const height = confirming ? 92 : 148;
        const top = rect.bottom + 6 + height > window.innerHeight ? Math.max(8, rect.top - height - 6) : rect.bottom + 6;
        const left = Math.max(8, Math.min(rect.right - width, window.innerWidth - width - 8));
        setPlace({ top, left });
    }, [anchor, confirming]);

    useEffect(() => {
        function onPointer(event: PointerEvent) {
            const target = event.target;
            if (!(target instanceof Node)) return;
            if (menu.current?.contains(target) || anchor?.contains(target)) return;
            onClose();
        }
        function onKey(event: globalThis.KeyboardEvent) {
            if (event.key === "Escape") onClose();
        }
        document.addEventListener("pointerdown", onPointer);
        document.addEventListener("keydown", onKey);
        return () => {
            document.removeEventListener("pointerdown", onPointer);
            document.removeEventListener("keydown", onKey);
        };
    }, [anchor, onClose]);

    if (!place) return null;

    return (
        <div
            ref={menu}
            role="menu"
            className="fixed z-50 w-[188px] rounded-xl border border-white/10 bg-[#141b30] p-1 text-sm shadow-[0_16px_50px_rgb(0_0_0_/_0.45)]"
            style={{ top: place.top, left: place.left }}
        >
            {confirming ? (
                <div className="px-2 py-2">
                    <p className="px-1 pb-2 text-[13px] font-medium">Delete this chat?</p>
                    <div className="flex gap-2">
                        <button
                            type="button"
                            className="h-8 flex-1 rounded-lg hover:bg-white/10"
                            onClick={() => setConfirming(false)}
                        >
                            Cancel
                        </button>
                        <button
                            type="button"
                            className="h-8 flex-1 rounded-lg bg-[#3b1020] font-semibold text-[#fda4af]"
                            onClick={onDelete}
                        >
                            Delete
                        </button>
                    </div>
                </div>
            ) : (
                <>
                    <MenuItem icon={<PencilIcon />} label="Rename" onClick={onRename} />
                    <MenuItem icon={<PinIcon />} label={pinned ? "Unpin" : "Pin"} onClick={onPin} />
                    <div className="my-1 h-px bg-white/10" />
                    <MenuItem icon={<TrashIcon />} label="Delete" danger onClick={() => setConfirming(true)} />
                </>
            )}
        </div>
    );
}

function MenuItem({
    icon,
    label,
    onClick,
    danger = false,
}: {
    icon: ReactNode;
    label: string;
    onClick: () => void;
    danger?: boolean;
}) {
    return (
        <button
            type="button"
            role="menuitem"
            onClick={onClick}
            className={cx(
                "flex h-9 w-full items-center gap-2 rounded-lg px-2 text-left",
                danger ? "text-[#fb7185] hover:bg-[#3b1020]" : "text-white hover:bg-white/10",
            )}
        >
            {icon}
            {label}
        </button>
    );
}
