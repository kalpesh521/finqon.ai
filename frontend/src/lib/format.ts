import type { Cell, ChartKind, Column } from "@/api/eventSchema";

export type ChartPoint = {
    label: string;
    value: number;
};

const inr = new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
});

const numberFormat = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });
const countFormat = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

const MONEY_NAME = /amount|spend|total|balance|fee|emi|salary|price|cost/i;
const COUNT_NAME = /(^|_)(count|n|txn_count)$/i;

export const CHART_PALETTE = [
    "#3B82F6",
    "#14B8A6",
    "#F59E0B",
    "#8B5CF6",
    "#EC4899",
    "#22C55E",
    "#F97316",
    "#6366F1",
] as const;

export function formatInr(value: number): string {
    return inr.format(value);
}

export function formatDay(iso: string): string {
    const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
    if (!match) return iso;
    const year = Number(match[1]);
    const month = Number(match[2]);
    const day = Number(match[3]);
    if (!year || !month || !day) return iso;
    return new Intl.DateTimeFormat("en-IN", {
        day: "numeric",
        month: "short",
        year: "numeric",
        timeZone: "UTC",
    }).format(new Date(Date.UTC(year, month - 1, day)));
}

export function formatRange(from: string | null, to: string | null): string {
    if (!from || !to) return "No dated transactions";
    return `${formatDay(from)} – ${formatDay(to)}`;
}

export function formatTime(iso: string): string {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "";
    return new Intl.DateTimeFormat("en-IN", {
        hour: "numeric",
        minute: "2-digit",
        timeZone: "Asia/Kolkata",
    }).format(date);
}

export function formatRuntime(elapsedMs: number): string {
    if (elapsedMs < 1000) return `${elapsedMs} ms`;
    return `${(elapsedMs / 1000).toFixed(elapsedMs < 10_000 ? 1 : 0)} s`;
}

export function formatCount(value: number): string {
    return countFormat.format(value);
}

export function formatAxisAmount(value: number, money: boolean): string {
    const compact = new Intl.NumberFormat("en-IN", {
        notation: "compact",
        maximumFractionDigits: 1,
    }).format(value);
    return money ? `₹${compact.replace("₹", "")}` : compact;
}

export function columnIndex(columns: Column[], name: string): number {
    return columns.findIndex((column) => column.name === name);
}

/** Points come from the result rows. The assistant's sentence is never read. */
export function chartPoints(columns: Column[], rows: Cell[][], x: string, y: string): ChartPoint[] {
    const xIndex = columnIndex(columns, x);
    const yIndex = columnIndex(columns, y);
    if (xIndex < 0 || yIndex < 0) return [];
    const points: ChartPoint[] = [];
    for (const row of rows) {
        const labelCell = row[xIndex];
        const valueCell = row[yIndex];
        if (typeof valueCell !== "number") continue;
        points.push({
            label: labelCell == null || labelCell === "" ? "—" : String(labelCell),
            value: valueCell,
        });
    }
    return points;
}

export function sumValues(points: ChartPoint[]): number {
    return points.reduce((sum, point) => sum + point.value, 0);
}

export function linePoints(points: ChartPoint[]): ChartPoint[] {
    const dated = points.map((point) => ({ point, time: Date.parse(point.label) }));
    if (dated.length === 0 || dated.some((item) => Number.isNaN(item.time))) return points;
    return [...dated]
        .sort((left, right) => left.time - right.time)
        .map((item) => item.point);
}

export function formatCell(column: Column, value: Cell | undefined): string {
    if (value == null) return "—";
    if (typeof value === "boolean") return value ? "Yes" : "No";
    if (typeof value === "number") {
        if (COUNT_NAME.test(column.name)) return countFormat.format(value);
        if (column.type === "number" && MONEY_NAME.test(column.name)) return formatInr(value);
        return numberFormat.format(value);
    }
    if (column.type === "date") return formatDay(value);
    return value;
}

export function isMoneyColumn(column: Column | undefined): boolean {
    return column?.type === "number" && MONEY_NAME.test(column.name);
}

export function axisLabel(label: string): string {
    return /^\d{4}-\d{2}-\d{2}/.test(label) ? formatDay(label) : label;
}

export function statusLabel(stage: "thinking" | "querying" | "summarizing"): string {
    if (stage === "thinking") return "Reading your statement…";
    if (stage === "querying") return "Running the SQL safely…";
    return "Preparing your answer…";
}

export function queryHeading(index: number, total: number): string | null {
    if (total < 2) return null;
    return `Query ${index + 1}`;
}

export type ChartChoice = Exclude<ChartKind, "none">;

export const CHART_CHOICES: ChartChoice[] = ["bar", "pie", "line"];
