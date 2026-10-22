import { useEffect, useRef, type KeyboardEvent } from "react";

import { SendIcon } from "@/components/icons";

type ComposerProps = {
    disabled: boolean;
    onSend: (text: string) => void;
};

export function Composer({ disabled, onSend }: ComposerProps) {
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
            <div className="flex items-center gap-2 rounded-[28px] border border-white/10 bg-[#070b14] p-2 shadow-[inset_0_1px_0_rgb(255_255_255_/_0.04)] focus-within:border-[#4c8dff]/70 focus-within:ring-4 focus-within:ring-[#4c8dff]/15">
                <label className="sr-only" htmlFor="question">
                    Ask a question about your transactions
                </label>
                <textarea
                    id="question"
                    ref={field}
                    rows={1}
                    maxLength={2000}
                    disabled={disabled}
                    placeholder="Ask about your transactions…"
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
