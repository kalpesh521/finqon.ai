import { useCallback, useEffect, useRef, useState } from "react";

import type { FileRecord } from "@/api/eventSchema";
import { ApiError, deleteFile, getFile, listFiles, uploadFile } from "@/api/http";
import { useAuth } from "@/auth/AuthProvider";
import { MAX_BATCH, validateFile } from "@/lib/files";

export type UploadPhase =
    | "staged"
    | "uploading"
    | "queued"
    | "processing"
    | "ready"
    | "failed"
    | "quarantined";

export type UploadItem = {
    key: string;
    name: string;
    size: number;
    phase: UploadPhase;
    progress: number;
    stage: string;
    serverId: string | null;
    message: string | null;
    rows: number;
    file: File | null;
};

const POLL_MS = 900;

function fromRecord(record: FileRecord): UploadItem {
    return {
        key: record.id,
        name: record.filename,
        size: record.size_bytes,
        phase: record.status,
        progress: record.progress,
        stage: record.stage,
        serverId: record.id,
        message: record.error ?? record.warnings[0] ?? null,
        rows: record.rows_imported,
        file: null,
    };
}

function isActive(item: UploadItem): boolean {
    return item.serverId !== null && (item.phase === "queued" || item.phase === "processing");
}

export function useUploads(onDataChanged: () => void) {
    const { authorizedFetch } = useAuth();
    const [items, setItems] = useState<UploadItem[]>([]);
    const [notice, setNotice] = useState<string | null>(null);
    const itemsRef = useRef(items);
    itemsRef.current = items;
    const changed = useRef(onDataChanged);
    changed.current = onDataChanged;

    const patch = useCallback((key: string, change: Partial<UploadItem>) => {
        setItems((current) => current.map((item) => (item.key === key ? { ...item, ...change } : item)));
    }, []);

    // Files uploaded in earlier sessions.
    useEffect(() => {
        let cancelled = false;
        void listFiles(authorizedFetch)
            .then((records) => {
                if (cancelled) return;
                setItems((current) => {
                    const known = new Set(current.map((item) => item.serverId));
                    const loaded = records.filter((r) => !known.has(r.id)).map(fromRecord);
                    return [...current, ...loaded];
                });
            })
            .catch(() => undefined);
        return () => {
            cancelled = true;
        };
    }, [authorizedFetch]);

    const active = items.some(isActive);
    useEffect(() => {
        if (!active) return;
        const timer = window.setInterval(() => {
            for (const item of itemsRef.current.filter(isActive)) {
                const id = item.serverId;
                if (!id) continue;
                void getFile(authorizedFetch, id)
                    .then((record) => {
                        const before = itemsRef.current.find((i) => i.key === item.key);
                        patch(item.key, { ...fromRecord(record), key: item.key });
                        if (record.status === "ready" && before?.phase !== "ready") changed.current();
                    })
                    .catch((error: unknown) => {
                        if (error instanceof ApiError && error.status === 404) {
                            setItems((current) => current.filter((i) => i.key !== item.key));
                        }
                    });
            }
        }, POLL_MS);
        return () => window.clearInterval(timer);
    }, [active, authorizedFetch, patch]);

    const stage = useCallback((files: File[]) => {
        setNotice(null);
        const room = Math.max(0, MAX_BATCH - itemsRef.current.filter((i) => i.phase === "staged").length);
        const added: UploadItem[] = [];
        const problems: string[] = [];
        for (const file of files.slice(0, room)) {
            const problem = validateFile(file);
            if (problem) {
                problems.push(`${file.name}: ${problem}`);
                continue;
            }
            added.push({
                key: crypto.randomUUID(),
                name: file.name,
                size: file.size,
                phase: "staged",
                progress: 0,
                stage: "Ready to upload",
                serverId: null,
                message: null,
                rows: 0,
                file,
            });
        }
        if (files.length > room) problems.push(`Only ${MAX_BATCH} files at a time.`);
        if (problems.length > 0) setNotice(problems.join(" "));
        if (added.length > 0) setItems((current) => [...added, ...current]);
    }, []);

    const send = useCallback(
        async (item: UploadItem) => {
            const file = item.file;
            if (!file) return;
            patch(item.key, { phase: "uploading", progress: 6, stage: "Uploading", message: null });
            try {
                const record = await uploadFile(authorizedFetch, file);
                const duplicate = itemsRef.current.some((i) => i.serverId === record.id && i.key !== item.key);
                if (duplicate) {
                    setItems((current) => current.filter((i) => i.key !== item.key));
                    setNotice(`${file.name} was already uploaded.`);
                    return;
                }
                patch(item.key, { ...fromRecord(record), key: item.key, file });
                if (record.status === "ready") changed.current();
            } catch (error) {
                const message = error instanceof Error ? error.message : "Upload failed.";
                patch(item.key, { phase: "failed", progress: 0, stage: "Failed", message });
            }
        },
        [authorizedFetch, patch],
    );

    const uploadStaged = useCallback(() => {
        for (const item of itemsRef.current.filter((i) => i.phase === "staged")) void send(item);
    }, [send]);

    const retry = useCallback(
        (key: string) => {
            const item = itemsRef.current.find((i) => i.key === key);
            if (item?.file) void send(item);
        },
        [send],
    );

    const remove = useCallback(
        async (key: string) => {
            const item = itemsRef.current.find((i) => i.key === key);
            if (!item) return;
            if (!item.serverId) {
                setItems((current) => current.filter((i) => i.key !== key));
                return;
            }
            try {
                await deleteFile(authorizedFetch, item.serverId);
                setItems((current) => current.filter((i) => i.key !== key));
                changed.current();
            } catch (error) {
                setNotice(error instanceof Error ? error.message : "Could not remove the file.");
            }
        },
        [authorizedFetch],
    );

    const clearStaged = useCallback(() => {
        setItems((current) => current.filter((i) => i.phase !== "staged"));
        setNotice(null);
    }, []);

    return {
        items,
        notice,
        stage,
        uploadStaged,
        retry,
        remove,
        clearStaged,
        readyFiles: items.filter((i) => i.phase === "ready"),
        busy: items.some((i) => i.phase === "uploading" || isActive(i)),
        hasStaged: items.some((i) => i.phase === "staged"),
    };
}

export type Uploads = ReturnType<typeof useUploads>;
