import type { Evidence } from "@/state/chatReducer";
import { displayedChart } from "@/state/chatReducer";
import type { ChatState } from "@/state/chatReducer";
import { EvidenceChart } from "@/components/desk/EvidenceChart";
import { ResultTable } from "@/components/desk/ResultTable";
import { CHART_CHOICES, chartPoints, columnIndex, formatInr, isMoneyColumn, sumValues } from "@/lib/format";
import { cx } from "@/lib/cx";

type EvidencePanelProps = {
    state: ChatState;
    onChart: (kind: "bar" | "pie" | "line") => void;
    framed?: boolean;
};

export function EvidencePanel({ state, onChart, framed = true }: EvidencePanelProps) {
    const evidence = state.evidence;
    return (
        <section
            className={
                framed
                    ? "flex h-full min-h-0 flex-col overflow-hidden rounded-3xl border border-white/10 bg-[#0A0E18]/90 p-4 shadow-panel backdrop-blur-xl sm:p-5"
                    : "flex min-h-0 flex-col overflow-hidden rounded-3xl border border-white/10 bg-white/[0.03] p-4"
            }
            aria-label="Evidence"
        >
            <header className="shrink-0">
                <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-muted">Evidence</p>
                <h2 className="mt-1 text-lg font-semibold leading-snug sm:text-xl">
                    {evidence?.title || "Latest query evidence"}
                </h2>
            </header>
            {evidence ? <EvidenceBody evidence={evidence} state={state} onChart={onChart} framed={framed} /> : <EmptyEvidence />}
        </section>
    );
}

function EvidenceBody({
    evidence,
    state,
    onChart,
    framed,
}: {
    evidence: Evidence;
    state: ChatState;
    onChart: (kind: "bar" | "pie" | "line") => void;
    framed: boolean;
}) {
    const kind = displayedChart(state);
    const points = chartPoints(evidence.columns, evidence.rows, evidence.chart.x, evidence.chart.y);
    const yColumn = evidence.columns[columnIndex(evidence.columns, evidence.chart.y)];
    const total = sumValues(points);
    const showChart = kind !== null && evidence.rows.length > 0 && points.length > 0;
    return (
        <div className={cx("no-scrollbar mt-4 min-h-0 flex-1", framed && "overflow-y-auto")}>
            {points.length > 0 ? (
                <p className="text-2xl font-semibold tabular-nums sm:text-3xl">
                    {isMoneyColumn(yColumn) ? formatInr(total) : total.toLocaleString("en-IN")}
                    <span className="ml-2 text-sm font-medium text-muted">total</span>
                </p>
            ) : null}
            {evidence.rows.length === 0 && evidence.columns.length === 0 ? (
                <p className="mt-3 rounded-2xl bg-white/5 px-4 py-6 text-sm text-muted">
                    No matching transactions were found for that question.
                </p>
            ) : null}
            {showChart && kind ? (
                <div className="mt-3">
                    <div className="mb-3 flex gap-1 rounded-2xl bg-black/30 p-1" role="group" aria-label="Chart type">
                        {CHART_CHOICES.map((choice) => (
                            <button
                                key={choice}
                                type="button"
                                aria-pressed={kind === choice}
                                onClick={() => onChart(choice)}
                                className={cx(
                                    "h-9 flex-1 rounded-xl text-sm font-semibold capitalize transition",
                                    kind === choice ? "bg-[#2F6BFF] text-white shadow-[0_6px_16px_rgb(47_107_255_/_0.28)]" : "text-muted hover:text-white",
                                )}
                            >
                                {choice}
                            </button>
                        ))}
                    </div>
                    <div className="rounded-2xl border border-white/10 bg-black/20 p-3">
                        <EvidenceChart
                            kind={kind}
                            columns={evidence.columns}
                            rows={evidence.rows}
                            x={evidence.chart.x}
                            y={evidence.chart.y}
                        />
                    </div>
                </div>
            ) : null}
            <div className="mt-3">
                <ResultTable columns={evidence.columns} rows={evidence.rows} />
            </div>
            {evidence.truncated ? (
                <p className="mt-2 text-xs text-muted">
                    Showing {evidence.rows.length} rows. The query was capped, so later rows are not in this table.
                </p>
            ) : evidence.rows.length > 0 ? (
                <p className="mt-2 text-xs text-muted">
                    {evidence.rows.length} {evidence.rows.length === 1 ? "row" : "rows"}
                </p>
            ) : null}
        </div>
    );
}

function EmptyEvidence() {
    return (
        <p className="mt-4 rounded-2xl border border-white/10 bg-white/[0.03] px-4 py-6 text-sm leading-6 text-muted">
            Ask a question to see the chart, table, and rows used to support the answer.
        </p>
    );
}
