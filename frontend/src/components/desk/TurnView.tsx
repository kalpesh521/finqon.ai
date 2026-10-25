import { AnimatePresence, motion } from "framer-motion";
import { useState } from "react";

import { SqlBlock } from "@/components/desk/SqlBlock";
import { ChevronIcon, CodeIcon, Mark, UploadCloudIcon } from "@/components/icons";
import { FOLLOW_UPS, STARTERS } from "@/lib/history";
import { cx } from "@/lib/cx";
import { formatTime, statusLabel } from "@/lib/format";
import type { Stage } from "@/api/eventSchema";
import type { Turn } from "@/state/chatReducer";

const STEPS: Stage[] = ["thinking", "querying", "summarizing"];

type TurnViewProps = {
    turn: Turn;
    isLast: boolean;
    streaming: boolean;
    initial: string;
    showEvidenceLink: boolean;
    onSuggest: (text: string) => void;
    onRetry: (question: string) => void;
};

export function TurnView({
    turn,
    isLast,
    streaming,
    initial,
    showEvidenceLink,
    onSuggest,
    onRetry,
}: TurnViewProps) {
    const [sqlOpen, setSqlOpen] = useState(false);
    const showStatus = turn.outcome === "pending" && turn.answer === "" && turn.stage !== null;
    const failed = turn.outcome === "error" || turn.outcome === "refused";
    return (
        <div className="space-y-3">
            <div className="flex justify-end">
                <div className="max-w-[min(100%,36rem)]">
                    <div className="rounded-3xl rounded-br-lg bg-gradient-to-br from-[#2F6BFF] to-[#1d4ed8] px-4 py-3 text-sm font-normal leading-6 text-white shadow-[0_10px_30px_rgb(47_107_255_/_0.18)]">
                        {turn.question}
                    </div>
                    <p className="mt-1 text-right text-[11px] text-muted">{formatTime(turn.createdAt)}</p>
                </div>
                <span className="ml-2 grid h-8 w-8 shrink-0 place-items-center rounded-full bg-[#2F6BFF] text-xs font-semibold text-white">
                    {initial}
                </span>
            </div>
            <div className="flex gap-2.5">
                <Mark className="h-8 w-8 shrink-0" />
                <div className="min-w-0 flex-1">
                    {showStatus && turn.stage ? <StatusSteps stage={turn.stage} /> : null}
                    {turn.answer ? (
                        <p className="whitespace-pre-wrap text-[15px] font-normal leading-7 text-white/90">{turn.answer}</p>
                    ) : null}
                    {turn.clarification ? (
                        <div className="mt-2">
                            <p className="text-sm leading-7">{turn.clarification.question}</p>
                            {turn.clarification.options.length > 0 ? (
                                <div className="mt-3 flex flex-wrap gap-2">
                                    {turn.clarification.options.map((option) => (
                                        <button
                                            key={option}
                                            type="button"
                                            disabled={streaming}
                                            onClick={() => onSuggest(option)}
                                            className="rounded-full border border-white/10 bg-white/[0.04] px-3.5 py-2 text-sm font-medium text-white/90 transition hover:border-[#7EB6FF]/50 hover:bg-white/[0.07] disabled:opacity-50"
                                        >
                                            {option}
                                        </button>
                                    ))}
                                </div>
                            ) : null}
                        </div>
                    ) : null}
                    {failed && turn.error ? (
                        <div className="mt-2 rounded-2xl border border-[#fb7185]/20 bg-[#3b1020] px-4 py-3 text-sm text-[#fda4af]" role="alert">
                            <p>{turn.error.message}</p>
                            <button
                                type="button"
                                className="mt-2 font-semibold text-[#fda4af] underline-offset-2 hover:underline"
                                onClick={() => onRetry(turn.question)}
                                disabled={streaming}
                            >
                                Try again
                            </button>
                        </div>
                    ) : null}
                    {turn.queries.length > 0 ? (
                        <div className="mt-3">
                            <button
                                type="button"
                                aria-expanded={sqlOpen}
                                onClick={() => setSqlOpen((open) => !open)}
                                className={cx(
                                    "inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-[13px] font-semibold transition",
                                    sqlOpen
                                        ? "border-[#4c8dff]/50 bg-[#4c8dff]/15 text-[#d6e4ff]"
                                        : "border-white/10 bg-white/[0.04] text-[#b9d0ff] hover:border-[#4c8dff]/40 hover:bg-white/[0.07]",
                                )}
                            >
                                <CodeIcon className="h-3.5 w-3.5" />
                                {sqlOpen ? "Hide SQL" : "Show SQL"}
                                <ChevronIcon className={cx("h-3.5 w-3.5 transition", sqlOpen && "rotate-180")} />
                            </button>
                            <AnimatePresence initial={false}>
                                {sqlOpen ? (
                                    <motion.div
                                        key="sql"
                                        initial={{ height: 0, opacity: 0 }}
                                        animate={{ height: "auto", opacity: 1 }}
                                        exit={{ height: 0, opacity: 0 }}
                                        transition={{ duration: 0.22, ease: "easeOut" }}
                                        className="overflow-hidden"
                                    >
                                        <SqlBlock queries={turn.queries} />
                                    </motion.div>
                                ) : null}
                            </AnimatePresence>
                        </div>
                    ) : null}
                    {isLast && turn.outcome === "answered" && !streaming ? (
                        <div className="mt-4 flex flex-wrap gap-2">
                            {FOLLOW_UPS.map((chip) => (
                                <button
                                    key={chip}
                                    type="button"
                                    onClick={() => onSuggest(chip)}
                                    className="rounded-full border border-white/10 bg-white/[0.04] px-3.5 py-2 text-left text-[13px] font-medium text-white/85 transition hover:border-[#7EB6FF]/50 hover:bg-white/[0.07]"
                                >
                                    {chip}
                                </button>
                            ))}
                        </div>
                    ) : null}
                    {showEvidenceLink ? (
                        <a href="#evidence" className="mt-3 inline-flex text-sm font-semibold text-[#9ec0ff]">
                            View chart and table
                        </a>
                    ) : null}
                </div>
            </div>
        </div>
    );
}

function StatusSteps({ stage }: { stage: Stage }) {
    const active = STEPS.indexOf(stage);
    return (
        <div className="max-w-md rounded-2xl border border-white/10 bg-white/[0.03] px-3.5 py-3">
            <p className="sr-only" aria-live="polite">
                {statusLabel(stage)}
            </p>
            <ol className="space-y-0">
                {STEPS.map((step, index) => {
                    const state = index < active ? "done" : index === active ? "active" : "wait";
                    return (
                        <li key={step} className="relative flex items-center gap-3 pb-3 last:pb-0">
                            {index < STEPS.length - 1 ? (
                                <span
                                    className={cx(
                                        "absolute left-[11px] top-6 bottom-0 w-px",
                                        index < active ? "bg-[#2F6BFF]/70" : "bg-white/10",
                                    )}
                                    aria-hidden="true"
                                />
                            ) : null}
                            <span
                                className={cx(
                                    "relative z-10 grid h-6 w-6 shrink-0 place-items-center rounded-full text-[11px] font-semibold",
                                    state === "done" && "bg-[#2F6BFF] text-white",
                                    state === "active" && "bg-[#2F6BFF]/20 text-[#d6e4ff] ring-1 ring-[#7EB6FF]/70",
                                    state === "wait" && "bg-white/[0.04] text-white/35 ring-1 ring-white/10",
                                )}
                            >
                                {state === "done" ? (
                                    <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                                        <path d="m3.5 8.2 3 3 6-6.4" strokeLinecap="round" strokeLinejoin="round" />
                                    </svg>
                                ) : state === "active" ? (
                                    <span className="finqon-step-dot h-2 w-2 rounded-full bg-[#70E7FF]" />
                                ) : (
                                    index + 1
                                )}
                            </span>
                            <span
                                className={cx(
                                    "flex min-w-0 items-center gap-2 text-sm",
                                    state === "wait" ? "text-white/35" : "text-white/90",
                                    state === "active" && "font-medium",
                                )}
                            >
                                <span className="truncate">{statusLabel(step)}</span>
                                {state === "active" ? (
                                    <span className="finqon-caret inline-flex gap-1" aria-hidden="true">
                                        <span className="h-1 w-1 rounded-full bg-[#70E7FF]" />
                                        <span className="h-1 w-1 rounded-full bg-[#70E7FF]" />
                                        <span className="h-1 w-1 rounded-full bg-[#70E7FF]" />
                                    </span>
                                ) : null}
                            </span>
                        </li>
                    );
                })}
            </ol>
        </div>
    );
}

const STARTER_NOTES = ["Last month", "All categories", "Top merchant"] as const;

export function EmptyThread({
    name,
    onSuggest,
    onUpload,
    uploadedCount,
}: {
    name: string;
    onSuggest: (text: string) => void;
    onUpload: () => void;
    uploadedCount: number;
}) {
    return (
        <div className="mx-auto flex w-full max-w-2xl flex-col items-center py-2 text-center">
            <div className="relative mb-3 grid h-20 w-20 place-items-center [@media(min-height:760px)]:mb-5 [@media(min-height:760px)]:h-24 [@media(min-height:760px)]:w-24" aria-hidden="true">
                <span className="finqon-logo-glow absolute h-14 w-14 rounded-full bg-[#3D8BFF]/45 blur-2xl" />
                <span className="finqon-logo-float relative grid h-16 w-16 place-items-center">
                    <span className="finqon-logo-track absolute inset-0 rounded-full" />
                    <span className="finqon-logo-arc absolute inset-0 rounded-full" />
                    <Mark className="relative h-12 w-12" />
                </span>
            </div>
            <p className="text-base font-medium text-white/55 sm:text-lg">Hello, {name}</p>
            <h2 className="mt-1 text-[1.6rem] font-semibold leading-tight sm:text-4xl">Turn transactions into insights.</h2>
            <div className="mt-5 grid w-full gap-2.5 sm:grid-cols-3 sm:gap-3">
                {STARTERS.map((starter, index) => (
                    <button
                        key={starter}
                        type="button"
                        onClick={() => onSuggest(starter)}
                        className="rounded-2xl border border-white/10 bg-white/[0.03] px-4 py-3 text-left transition hover:border-[#7EB6FF]/45 hover:bg-white/[0.06] sm:py-4"
                    >
                        <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[#8eb6ff]">
                            {STARTER_NOTES[index]}
                        </span>
                        <span className="mt-1.5 block text-sm font-medium leading-6 text-white/90">{starter}</span>
                    </button>
                ))}
            </div>
            <button
                type="button"
                onClick={onUpload}
                className="group mt-3 flex w-full items-center gap-3 rounded-2xl border border-dashed border-[#4c8dff]/45 bg-[#4c8dff]/[0.04] px-4 py-3 text-left transition hover:border-[#70E7FF]/70 hover:bg-[#4c8dff]/[0.09] sm:mt-4"
            >
                <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-[#4c8dff]/15 text-[#8eb6ff]">
                    <UploadCloudIcon className="h-6 w-6" />
                </span>
                <span className="min-w-0 flex-1">
                    <span className="block text-sm font-semibold text-white/90">
                        {uploadedCount > 0
                            ? `${uploadedCount} statement${uploadedCount === 1 ? "" : "s"} uploaded. Add more`
                            : "Upload your statements"}
                    </span>
                    <span className="block truncate text-xs text-white/45">
                        Drag &amp; drop or choose CSV, XLSX, PDF. Then chat with them.
                    </span>
                </span>
                <span className="hidden shrink-0 rounded-full border border-white/10 px-3 py-1 text-xs font-semibold text-[#9ec0ff] transition group-hover:border-[#70E7FF]/50 sm:block">
                    Choose files
                </span>
            </button>
        </div>
    );
}
