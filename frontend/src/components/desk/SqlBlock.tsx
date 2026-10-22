import { useState } from "react";

import type { QueryView } from "@/state/chatReducer";
import { formatRuntime, queryHeading } from "@/lib/format";

type SqlBlockProps = {
    queries: QueryView[];
};

export function SqlBlock({ queries }: SqlBlockProps) {
    const [copied, setCopied] = useState<number | null>(null);
    if (queries.length === 0) return null;

    async function copy(index: number, sql: string) {
        try {
            await navigator.clipboard.writeText(sql);
            setCopied(index);
        } catch {
            setCopied(-1);
        }
        window.setTimeout(() => setCopied(null), 1500);
    }

    return (
        <div className="mt-3 space-y-3">
            {queries.map((query, index) => {
                const heading = queryHeading(index, queries.length);
                return (
                    <div key={`${query.purpose}-${index}`} className="overflow-hidden rounded-2xl border border-white/10 bg-[#070b14]">
                        <div className="flex items-center justify-between gap-3 px-3.5 py-2.5">
                            <div className="min-w-0">
                                {heading ? <p className="text-xs font-semibold text-ink">{heading}</p> : null}
                                {query.purpose ? <p className="text-xs text-muted">{query.purpose}</p> : null}
                                <p className="text-xs text-muted">
                                    {query.elapsedMs == null ? "Runtime not stored" : formatRuntime(query.elapsedMs)}
                                    {query.settled
                                        ? ` · ${query.rows.length} ${query.rows.length === 1 ? "row" : "rows"}`
                                        : " · no rows returned"}
                                </p>
                            </div>
                            <button
                                type="button"
                                className="shrink-0 rounded-full border border-white/10 bg-white/[0.04] px-3 py-1 text-xs font-semibold text-[#b9d0ff] transition hover:border-[#4c8dff]/40 hover:bg-[#4c8dff]/15"
                                onClick={() => void copy(index, query.sql)}
                            >
                                {copied === index ? "Copied" : copied === -1 ? "Copy failed" : "Copy"}
                            </button>
                        </div>
                        <pre className="overflow-x-hidden whitespace-pre-wrap break-words border-t border-white/10 px-3.5 py-3 font-mono text-[12.5px] leading-6 text-[#dbe4ff]">
                            {query.sql}
                        </pre>
                    </div>
                );
            })}
        </div>
    );
}
