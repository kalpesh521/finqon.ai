import { useEffect, useRef, type KeyboardEvent } from "react";

import { FileIcon, PaperclipIcon, SendIcon } from "@/components/icons";
import type { UploadItem } from "@/hooks/useUploads";

type ComposerProps = {
    disabled: boolean;
    onSend: (text: string) => void;
    files: UploadItem[];
    onAttach: () => void;
};

export function Composer({ disabled, onSend, files, onAttach }: ComposerProps) {
    const field = useRef<HTMLTextAreaElement>(null);

    useEffect(() => {
        if (!disabled) field.current?.focus();
    }, [disabled]);

    function resize(element: HTMLTextAreaElement) {
        element.style.height = "auto";
        element.style.height = `${Math.min(element.scrollHeight, 112)}px`;
    }

    function submit() {
        const element = field.current;
        const value = element?.value ?? "";
        if (!value.trim() || disabled || !element) return;
        onSend(value);
        element.value = "";
        element.style.height = "auto";
    }

    function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
        if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault();
            submit();
        }
    }

    return (
        <form
            className="shrink-0 px-4 pb-4 pt-2"
            onSubmit={(event) => {
                event.preventDefault();
                submit();
            }}
        >
            {files.length > 0 ? (
                <ul className="no-scrollbar mb-2 flex gap-2 overflow-x-auto px-1" aria-label="Files in this chat">
                    {files.map((file) => (
                        <li
                            key={file.key}
                            className="flex max-w-[220px] shrink-0 items-center gap-1.5 rounded-full border border-[#4c8dff]/30 bg-[#4c8dff]/10 py-1 pl-2 pr-3 text-xs font-medium text-[#cfe0ff]"
                        >
                            <FileIcon className="h-3.5 w-3.5 shrink-0" />
                            <span className="truncate" title={file.name}>
                                {file.name}
                            </span>
                        </li>
                    ))}
                </ul>
            ) : null}
            <div className="flex items-center gap-2 rounded-[28px] border border-white/10 bg-[#070b14] p-2 shadow-[inset_0_1px_0_rgb(255_255_255_/_0.04)] focus-within:border-[#4c8dff]/70 focus-within:ring-4 focus-within:ring-[#4c8dff]/15">
                <label className="sr-only" htmlFor="question">
                    Ask a question about your transactions
                </label>
                <button
                    type="button"
                    onClick={onAttach}
                    aria-label="Upload statements"
                    title="Upload statements"
                    className="grid h-11 w-11 shrink-0 place-items-center rounded-full border border-white/10 bg-white/[0.04] text-white/70 transition hover:border-[#4c8dff]/50 hover:bg-white/[0.08] hover:text-white"
                >
                    <PaperclipIcon />
                </button>
                <textarea
                    id="question"
                    ref={field}
                    rows={1}
                    maxLength={2000}
                    disabled={disabled}
                    placeholder={
                        files.length > 0
                            ? "Ask me anything about these statements…"
                            : "What did you spend on? Ask about merchants, trends, anything…"
                    }
                    onKeyDown={onKeyDown}
                    onInput={(event) => resize(event.currentTarget)}
                    className="no-scrollbar max-h-28 min-h-11 flex-1 resize-none bg-transparent px-2 py-2 text-base leading-6 text-white outline-none placeholder:text-white/35 disabled:opacity-60 sm:text-sm sm:leading-5"
                />
                <button
                    type="submit"
                    disabled={disabled}
                    aria-label="Send"
                    className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-[#2F6BFF] text-white shadow-[0_8px_24px_rgb(47_107_255_/_0.35)] transition hover:bg-[#3d78ff] disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-white/30 disabled:shadow-none"
                >
                    <SendIcon />
                </button>
            </div>
        </form>
    );
}
