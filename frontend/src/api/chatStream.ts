import { ZodError } from "zod";

import { parseChatEvent, type ChatEvent } from "@/api/eventSchema";

export function takeFrames(buffer: string): { frames: string[]; rest: string } {
    const parts = buffer.split("\n\n");
    const rest = parts.pop() ?? "";
    return { frames: parts.filter((frame) => frame.trim().length > 0), rest };
}

export function parseSseFrame(frame: string): unknown {
    const data: string[] = [];
    for (const rawLine of frame.split("\n")) {
        const line = rawLine.replace(/\r$/, "");
        if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
    }
    if (data.length === 0) return null;
    return JSON.parse(data.join("\n")) as unknown;
}

export async function readChatStream(
    response: Response,
    onEvent: (event: ChatEvent) => void,
): Promise<void> {
    const body = response.body;
    if (!body) throw new Error("The answer stream was empty.");
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    try {
        for (;;) {
            const chunk = await reader.read();
            if (chunk.done) break;
            buffer += decoder.decode(chunk.value, { stream: true });
            const taken = takeFrames(buffer);
            buffer = taken.rest;
            for (const frame of taken.frames) emitFrame(frame, onEvent);
        }
        buffer += decoder.decode();
        if (buffer.trim()) emitFrame(buffer, onEvent);
    } catch (error) {
        if (error instanceof ZodError) {
            throw new Error("The answer stream did not match the API contract.");
        }
        throw error;
    }
}

function emitFrame(frame: string, onEvent: (event: ChatEvent) => void): void {
    const payload = parseSseFrame(frame);
    if (payload === null) return;
    onEvent(parseChatEvent(payload));
}
