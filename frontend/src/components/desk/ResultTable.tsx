import type { Column, Cell } from "@/api/eventSchema";
import { formatCell } from "@/lib/format";
import { cx } from "@/lib/cx";

type ResultTableProps = {
    columns: Column[];
    rows: Cell[][];
};

export function ResultTable({ columns, rows }: ResultTableProps) {
    if (columns.length === 0) return null;
    return (
        <div className="no-scrollbar overflow-x-auto rounded-2xl border border-white/10">
            <table className="w-full border-collapse text-left text-sm">
                <caption className="sr-only">Query result</caption>
                <thead className="sticky top-0 bg-[#101624] text-xs font-medium text-muted">
                    <tr>
                        {columns.map((column) => (
                            <th
                                key={column.name}
                                scope="col"
                                className={cx(
                                    "px-3 py-2 font-semibold",
                                    column.type === "number" && "text-right",
                                )}
                            >
                                {column.name}
                            </th>
                        ))}
                    </tr>
                </thead>
                <tbody>
                    {rows.length === 0 ? (
                        <tr>
                            <td colSpan={columns.length} className="px-3 py-6 text-center text-muted">
                                Nothing in your statement matched that question.
                            </td>
                        </tr>
                    ) : (
                        rows.map((row, rowIndex) => (
                            <tr key={rowIndex} className="border-t border-line">
                                {columns.map((column, columnIndex) => {
                                    const value = row[columnIndex];
                                    const negative = typeof value === "number" && value < 0;
                                    return (
                                        <td
                                            key={column.name}
                                            className={cx(
                                                "px-3 py-2.5 tabular-nums",
                                                column.type === "number" ? "whitespace-nowrap text-right" : "max-w-[11rem] truncate",
                                                negative && "text-[#fb7185]",
                                            )}
                                        >
                                            {formatCell(column, value)}
                                        </td>
                                    );
                                })}
                            </tr>
                        ))
                    )}
                </tbody>
            </table>
        </div>
    );
}
