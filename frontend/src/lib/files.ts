export const MAX_FILE_BYTES = 10 * 1024 * 1024;
export const MAX_BATCH = 5;
export const ACCEPT = ".csv,.tsv,.txt,.xlsx,.pdf";
const EXTENSIONS = [".csv", ".tsv", ".txt", ".xlsx", ".pdf"];

export function validateFile(file: File): string | null {
    const name = file.name.toLowerCase();
    if (!EXTENSIONS.some((ext) => name.endsWith(ext))) return "Use CSV, XLSX or PDF.";
    if (file.size === 0) return "The file is empty.";
    if (file.size > MAX_FILE_BYTES) return "Larger than 10 MB.";
    return null;
}

export function formatBytes(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
