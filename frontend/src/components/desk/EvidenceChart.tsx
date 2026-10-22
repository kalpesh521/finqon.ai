import {
    CartesianGrid,
    Cell,
    Line,
    LineChart,
    Pie,
    PieChart,
    ResponsiveContainer,
    Tooltip,
    XAxis,
    YAxis,
} from "recharts";

import type { Cell as TableCell, Column } from "@/api/eventSchema";
import {
    CHART_PALETTE,
    axisLabel,
    chartPoints,
    columnIndex,
    formatAxisAmount,
    formatInr,
    isMoneyColumn,
    linePoints,
    type ChartPoint,
} from "@/lib/format";

type EvidenceChartProps = {
    kind: "bar" | "pie" | "line";
    columns: Column[];
    rows: TableCell[][];
    x: string;
    y: string;
};

export function EvidenceChart({ kind, columns, rows, x, y }: EvidenceChartProps) {
    const points = chartPoints(columns, rows, x, y);
    if (points.length === 0) {
        return (
            <p className="rounded-2xl bg-white/5 px-4 py-6 text-sm text-muted">
                These rows have no numeric values to plot.
            </p>
        );
    }
    const yColumn = columns[columnIndex(columns, y)];
    const money = isMoneyColumn(yColumn);
    const formatValue = (value: number) => (money ? formatInr(value) : value.toLocaleString("en-IN"));
    if (kind === "pie") return <Donut points={points} formatValue={formatValue} />;
    if (kind === "line") return <Trend points={points} formatValue={formatValue} money={money} />;
    return <Bars points={points} formatValue={formatValue} />;
}

function Bars({ points, formatValue }: { points: ChartPoint[]; formatValue: (value: number) => string }) {
    const peak = Math.max(...points.map((point) => Math.abs(point.value)), 0);
    return (
        <div className="space-y-2.5" role="img" aria-label="Bar chart of the query result">
            {points.map((point, index) => {
                const width = peak === 0 ? 0 : (Math.abs(point.value) / peak) * 100;
                return (
                    <div key={`${point.label}-${index}`}>
                        <div className="mb-1 flex items-baseline justify-between gap-3 text-sm">
                            <span className="truncate font-medium">{axisLabel(point.label)}</span>
                            <span className="shrink-0 tabular-nums text-muted">{formatValue(point.value)}</span>
                        </div>
                        <div className="h-3 rounded-full bg-white/10">
                            <div
                                className="h-3 rounded-full"
                                style={{
                                    width: `${width}%`,
                                    background: CHART_PALETTE[index % CHART_PALETTE.length],
                                }}
                            />
                        </div>
                    </div>
                );
            })}
        </div>
    );
}

function Donut({ points, formatValue }: { points: ChartPoint[]; formatValue: (value: number) => string }) {
    const slices = points
        .filter((point) => point.value !== 0)
        .map((point) => ({ ...point, magnitude: Math.abs(point.value) }));
    const total = slices.reduce((sum, slice) => sum + slice.magnitude, 0);
    return (
        <div>
            <div className="mx-auto h-[168px] w-[168px]" role="img" aria-label="Donut chart of the query result">
                <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                        <Pie
                            data={slices}
                            dataKey="magnitude"
                            nameKey="label"
                            innerRadius={50}
                            outerRadius={74}
                            paddingAngle={1.5}
                            stroke="#0A0E18"
                        >
                            {slices.map((slice, index) => (
                                <Cell key={`${slice.label}-${index}`} fill={CHART_PALETTE[index % CHART_PALETTE.length]} />
                            ))}
                        </Pie>
                        <Tooltip
                            formatter={(value) => formatValue(asNumber(value))}
                            contentStyle={TOOLTIP_STYLE}
                            itemStyle={{ color: "#F7FAFF" }}
                        />
                    </PieChart>
                </ResponsiveContainer>
            </div>
            <ul className="mt-3 grid grid-cols-1 gap-y-1.5 text-[12px]">
                {points.map((point, index) => {
                    const share = total === 0 ? 0 : (Math.abs(point.value) / total) * 100;
                    return (
                        <li key={`${point.label}-${index}`} className="flex min-w-0 items-center justify-between gap-2">
                            <span className="flex min-w-0 items-center gap-1.5">
                                <span
                                    className="h-2 w-2 shrink-0 rounded-full"
                                    style={{ background: CHART_PALETTE[index % CHART_PALETTE.length] }}
                                />
                                <span className="truncate">{axisLabel(point.label)}</span>
                            </span>
                            <span className="shrink-0 tabular-nums font-medium">
                                {formatValue(point.value)}
                                <span className="ml-1.5 font-normal text-muted">{share.toFixed(0)}%</span>
                            </span>
                        </li>
                    );
                })}
            </ul>
        </div>
    );
}

function Trend({
    points,
    formatValue,
    money,
}: {
    points: ChartPoint[];
    formatValue: (value: number) => string;
    money: boolean;
}) {
    const series = linePoints(points).map((point) => ({ ...point, axis: axisLabel(point.label) }));
    return (
        <div className="h-[200px]" role="img" aria-label="Line chart of the query result">
            <ResponsiveContainer width="100%" height="100%">
                <LineChart data={series} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                    <CartesianGrid stroke="rgba(255,255,255,0.08)" vertical={false} />
                    <XAxis dataKey="axis" tick={{ fontSize: 11, fill: "#93a0bd" }} axisLine={false} tickLine={false} />
                    <YAxis
                        tickFormatter={(value: number) => formatAxisAmount(value, money)}
                        width={56}
                        tick={{ fontSize: 11, fill: "#93a0bd" }}
                        axisLine={false}
                        tickLine={false}
                    />
                    <Tooltip
                        formatter={(value) => formatValue(asNumber(value))}
                        contentStyle={TOOLTIP_STYLE}
                        itemStyle={{ color: "#F7FAFF" }}
                    />
                    <Line type="monotone" dataKey="value" stroke="#70E7FF" strokeWidth={2.5} dot={{ r: 3, fill: "#2F6BFF", stroke: "#70E7FF" }} />
                </LineChart>
            </ResponsiveContainer>
        </div>
    );
}

const TOOLTIP_STYLE = {
    background: "#12182c",
    border: "1px solid rgba(255,255,255,0.1)",
    borderRadius: 12,
    color: "#F7FAFF",
    fontSize: 12,
};

function asNumber(value: unknown): number {
    if (typeof value === "number") return value;
    if (typeof value === "string") {
        const parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : 0;
    }
    if (Array.isArray(value)) return asNumber(value[0]);
    return 0;
}
