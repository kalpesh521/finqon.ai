import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useRef, useState, type DragEvent } from "react";

import {
    CheckCircleIcon,
    CloseIcon,
    FileIcon,
    RetryIcon,
    TrashIcon,
    UploadCloudIcon,
} from "@/components/icons";
import type { UploadItem, Uploads } from "@/hooks/useUploads";
import { cx } from "@/lib/cx";
import { ACCEPT, formatBytes } from "@/lib/files";

type UploadDialogProps = {
    open: boolean;
    uploads: Uploads;
    onClose: () => void;
};

export function UploadDialog({ open, uploads, onClose }: UploadDialogProps) {
    const input = useRef<HTMLInputElement>(null);
    const panel = useRef<HTMLDivElement>(null);
    const [over, setOver] = useState(false);

    useEffect(() => {
        if (!open) return;
        panel.current?.focus();
        function onKey(event: KeyboardEvent) {
            if (event.key === "Escape") onClose();
        }
        document.addEventListener("keydown", onKey);
        return () => document.removeEventListener("keydown", onKey);
    }, [open, onClose]);

    function onDrop(event: DragEvent) {
        event.preventDefault();
        event.stopPropagation();
        setOver(false);
        uploads.stage(Array.from(event.dataTransfer.files));
    }

    const hasWork = uploads.items.length > 0;
    return (
        <AnimatePresence>
            {open ? (
                <motion.div
                    className="fixed inset-0 z-50 grid place-items-center p-3 sm:p-6"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                >
                    <button
                        type="button"
                        aria-label="Close upload"
                        className="absolute inset-0 bg-[#05070F]/75 backdrop-blur-sm"
                        onClick={onClose}
                    />
                    <motion.div
                        ref={panel}
                        role="dialog"
                        aria-modal="true"
                        aria-labelledby="upload-title"
                        tabIndex={-1}
                        initial={{ y: 16, scale: 0.98 }}
                        animate={{ y: 0, scale: 1 }}
                        exit={{ y: 16, scale: 0.98 }}
                        transition={{ type: "spring", stiffness: 340, damping: 30 }}
                        className="relative flex max-h-[min(92dvh,720px)] w-full max-w-[560px] flex-col overflow-hidden rounded-3xl border border-white/10 bg-[#0A0E18] shadow-panel outline-none"
                    >
                        <header className="flex items-start justify-between gap-3 px-5 pb-3 pt-5 sm:px-6">
                            <div>
                                <h2 id="upload-title" className="text-base font-semibold sm:text-lg">
                                    Upload statements
                                </h2>
                                <p className="mt-1 text-xs leading-5 text-white/50">
                                    CSV, XLSX or text PDF, up to 10 MB each. Then ask about them in chat.
                                </p>
                            </div>
                            <button
                                type="button"
                                aria-label="Close"
                                onClick={onClose}
                                className="grid h-9 w-9 shrink-0 place-items-center rounded-full text-white/60 transition hover:bg-white/10 hover:text-white"
                            >
                                <CloseIcon />
                            </button>
                        </header>

                        <div className="no-scrollbar min-h-0 flex-1 space-y-4 overflow-y-auto px-5 pb-2 sm:px-6">
                            <div
                                onDragOver={(event) => {
                                    event.preventDefault();
                                    setOver(true);
                                }}
                                onDragLeave={() => setOver(false)}
                                onDrop={onDrop}
                                className={cx(
                                    "flex flex-col items-center rounded-2xl border border-dashed px-4 text-center transition",
                                    hasWork ? "py-5" : "py-8 sm:py-10",
                                    over
                                        ? "border-[#70E7FF] bg-[#70E7FF]/10"
                                        : "border-[#4c8dff]/45 bg-[#4c8dff]/[0.04]",
                                )}
                            >
                                <UploadCloudIcon className={cx("text-[#8eb6ff]", hasWork ? "h-8 w-8" : "h-11 w-11")} />
                                <p className="mt-2 text-sm font-medium text-white/85 sm:text-[15px]">
                                    Drag &amp; drop or{" "}
                                    <button
                                        type="button"
                                        onClick={() => input.current?.click()}
                                        className="font-semibold text-[#70E7FF] underline-offset-2 hover:underline focus-visible:underline"
                                    >
                                        choose files
                                    </button>{" "}
                                    to upload
                                </p>
                                <p className="mt-1 text-xs text-white/40">CSV, XLSX &amp; PDF format up to 10 MB</p>
                                <input
                                    ref={input}
                                    type="file"
                                    multiple
                                    accept={ACCEPT}
                                    className="sr-only"
                                    tabIndex={-1}
                                    onChange={(event) => {
                                        uploads.stage(Array.from(event.target.files ?? []));
                                        event.target.value = "";
                                    }}
                                />
                            </div>

                            {uploads.notice ? (
                                <p role="alert" className="rounded-xl border border-[#fb7185]/25 bg-[#3b1020] px-3 py-2 text-xs text-[#fda4af]">
                                    {uploads.notice}
                                </p>
                            ) : null}

                            {hasWork ? (
                                <ul className="space-y-2.5 pb-1" aria-label="Files">
                                    <AnimatePresence initial={false}>
                                        {uploads.items.map((item) => (
                                            <FileRow key={item.key} item={item} uploads={uploads} />
                                        ))}
                                    </AnimatePresence>
                                </ul>
                            ) : null}
                        </div>

                        <footer className="flex items-center justify-end gap-2.5 border-t border-white/10 px-5 py-4 sm:px-6">
                            <button
                                type="button"
                                onClick={() => {
                                    uploads.clearStaged();
                                    onClose();
                                }}
                                className="h-10 rounded-xl border border-white/10 bg-white/[0.05] px-5 text-sm font-medium text-white/80 transition hover:bg-white/10"
                            >
                                {uploads.hasStaged ? "Cancel" : "Close"}
                            </button>
                            <button
                                type="button"
                                disabled={!uploads.hasStaged}
                                onClick={uploads.uploadStaged}
                                className="h-10 rounded-xl bg-[#2F6BFF] px-6 text-sm font-semibold text-white shadow-[0_8px_24px_rgb(47_107_255_/_0.3)] transition hover:bg-[#3d78ff] disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-white/30 disabled:shadow-none"
                            >
                                Upload
                            </button>
                        </footer>
                    </motion.div>
                </motion.div>
            ) : null}
        </AnimatePresence>
    );
}

const PHASE_COLOR: Record<UploadItem["phase"], string> = {
    staged: "text-white/45",
    uploading: "text-[#70E7FF]",
    queued: "text-[#70E7FF]",
    processing: "text-[#70E7FF]",
    ready: "text-[#4ade80]",
    failed: "text-[#fda4af]",
    quarantined: "text-[#fbbf24]",
};

function FileRow({ item, uploads }: { item: UploadItem; uploads: Uploads }) {
    const working = item.phase === "uploading" || item.phase === "queued" || item.phase === "processing";
    const bad = item.phase === "failed" || item.phase === "quarantined";
    const label =
        item.phase === "ready"
            ? `${item.rows.toLocaleString("en-IN")} transactions`
            : item.phase === "staged"
              ? formatBytes(item.size)
              : item.stage;
    return (
        <motion.li
            layout
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, height: 0, marginTop: 0 }}
            className="rounded-2xl border border-white/10 bg-white/[0.03] px-3.5 py-3"
        >
            <div className="flex items-center gap-3">
                <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-white/[0.06] text-[#9ec0ff]">
                    <FileIcon />
                </span>
                <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-white/90" title={item.name}>
                        {item.name}
                    </p>
                    <p className="mt-0.5 flex min-w-0 items-center gap-2 text-xs">
                        <span className="shrink-0 text-white/40">{formatBytes(item.size)}</span>
                        <span className={cx("truncate font-medium", PHASE_COLOR[item.phase])} aria-live="polite">
                            {label}
                        </span>
                    </p>
                </div>
                <RowAction item={item} uploads={uploads} />
            </div>
            {working || item.phase === "ready" ? (
                <div
                    className="mt-2.5 h-1.5 overflow-hidden rounded-full bg-white/10"
                    role="progressbar"
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={item.progress}
                    aria-label={`${item.name} progress`}
                >
                    <div
                        className={cx(
                            "h-full rounded-full transition-[width] duration-500 ease-out",
                            item.phase === "ready"
                                ? "bg-[#4ade80]"
                                : "finqon-progress bg-gradient-to-r from-[#2F6BFF] to-[#70E7FF]",
                        )}
                        style={{ width: `${Math.max(item.progress, 4)}%` }}
                    />
                </div>
            ) : null}
            {bad && item.message ? (
                <p
                    className={cx(
                        "mt-2 text-xs leading-5",
                        item.phase === "failed" ? "text-[#fda4af]" : "text-[#fbbf24]",
                    )}
                    role="alert"
                >
                    {item.message}
                </p>
            ) : null}
            {item.phase === "ready" && item.message ? (
                <p className="mt-2 text-xs leading-5 text-white/45">{item.message}</p>
            ) : null}
        </motion.li>
    );
}

function RowAction({ item, uploads }: { item: UploadItem; uploads: Uploads }) {
    const button =
        "grid h-9 w-9 shrink-0 place-items-center rounded-full transition hover:bg-white/10 focus-visible:bg-white/10";
    if (item.phase === "ready") {
        return (
            <div className="flex items-center">
                <CheckCircleIcon className="h-5 w-5 text-[#4ade80]" />
                <button type="button" aria-label={`Remove ${item.name}`} className={cx(button, "text-white/45 hover:text-[#fda4af]")} onClick={() => void uploads.remove(item.key)}>
                    <TrashIcon />
                </button>
            </div>
        );
    }
    if (item.phase === "failed" && item.file) {
        return (
            <div className="flex items-center">
                <button type="button" aria-label={`Retry ${item.name}`} className={cx(button, "text-white/70")} onClick={() => uploads.retry(item.key)}>
                    <RetryIcon />
                </button>
                <button type="button" aria-label={`Remove ${item.name}`} className={cx(button, "text-white/45")} onClick={() => void uploads.remove(item.key)}>
                    <CloseIcon />
                </button>
            </div>
        );
    }
    if (item.phase === "uploading" || item.phase === "queued" || item.phase === "processing") {
        return (
            <span className="grid h-9 w-9 place-items-center" aria-hidden="true">
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-[#70E7FF]/25 border-t-[#70E7FF]" />
            </span>
        );
    }
    return (
        <button
            type="button"
            aria-label={`Remove ${item.name}`}
            className={cx(button, "text-white/45 hover:text-[#fda4af]")}
            onClick={() => void uploads.remove(item.key)}
        >
            <TrashIcon />
        </button>
    );
}
