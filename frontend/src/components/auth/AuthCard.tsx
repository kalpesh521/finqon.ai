import { useState, type FormEvent } from "react";

import { ApiError } from "@/api/http";
import { useAuth } from "@/auth/AuthProvider";
import { EyeIcon } from "@/components/icons";
import { cx } from "@/lib/cx";

type Mode = "login" | "register";

export function AuthCard({ align = "start" }: { align?: "start" | "center" }) {
    const auth = useAuth();
    const [mode, setMode] = useState<Mode>("login");
    const [name, setName] = useState("");
    const [email, setEmail] = useState("");
    const [password, setPassword] = useState("");
    const [confirm, setConfirm] = useState("");
    const [showPassword, setShowPassword] = useState(false);
    const keep = true;
    const [error, setError] = useState<string | null>(null);
    const [pending, setPending] = useState(false);

    async function onSubmit(event: FormEvent<HTMLFormElement>) {
        event.preventDefault();
        const problem = validate(mode, name, email, password, confirm);
        if (problem) {
            setError(problem);
            return;
        }
        setPending(true);
        setError(null);
        try {
            if (mode === "login") await auth.login(email, password, keep);
            else await auth.register(name, email, password, keep);
        } catch (caught) {
            setError(caught instanceof ApiError ? caught.message : "Could not reach FinQon.ai. Try again.");
        } finally {
            setPending(false);
        }
    }

    return (
        <div className="w-full min-w-0 max-w-[400px] overflow-hidden rounded-[24px] border border-white/10 bg-[#0A0E18] px-4 py-3 text-[#F4F7FF] shadow-[0_18px_50px_rgba(0,0,0,0.35)] sm:px-6 sm:py-4">
            <div className="grid grid-cols-2 rounded-2xl bg-[#05070F] p-1" role="group" aria-label="Sign in or register">
                <ToggleButton active={mode === "login"} onClick={() => switchMode(setMode, setError, "login")}>
                    Sign in
                </ToggleButton>
                <ToggleButton active={mode === "register"} onClick={() => switchMode(setMode, setError, "register")}>
                    Register
                </ToggleButton>
            </div>
            <h2 className={cx("mt-3 text-lg font-semibold leading-7", align === "center" && "text-center")}>
                {mode === "login" ? "Welcome back" : "Create your account"}
            </h2>
            <form className="mt-3 flex w-full min-w-0 flex-col gap-3" onSubmit={(event) => void onSubmit(event)} noValidate>
                {mode === "register" ? (
                    <label className="flex w-full min-w-0 flex-col gap-1 text-sm font-medium leading-5 text-[#F4F7FF]" htmlFor="name">
                        Name
                        <input
                            id="name"
                            type="text"
                            autoComplete="name"
                            value={name}
                            onChange={(event) => setName(event.target.value)}
                            className={fieldClass}
                            placeholder="Your name"
                            required
                        />
                    </label>
                ) : null}
                <label className="flex w-full min-w-0 flex-col gap-1 text-sm font-medium leading-5 text-[#F4F7FF]" htmlFor="email">
                    Email
                    <input
                        id="email"
                        type="email"
                        autoComplete="email"
                        value={email}
                        onChange={(event) => setEmail(event.target.value)}
                        className={fieldClass}
                        placeholder="Enter your email"
                        required
                    />
                </label>
                <label className="flex w-full min-w-0 flex-col gap-1 text-sm font-medium leading-5 text-[#F4F7FF]" htmlFor="password">
                    Password
                    <span className="relative block w-full min-w-0">
                        <input
                            id="password"
                            type={showPassword ? "text" : "password"}
                            autoComplete={mode === "login" ? "current-password" : "new-password"}
                            value={password}
                            onChange={(event) => setPassword(event.target.value)}
                            className={`${fieldClass} pr-12`}
                            placeholder={mode === "register" ? "10+ characters" : "Enter password"}
                            required
                        />
                        <button
                            type="button"
                            className="absolute right-3 top-1/2 -translate-y-1/2 text-[#D5DEF2]"
                            onClick={() => setShowPassword((value) => !value)}
                            aria-label={showPassword ? "Hide password" : "Show password"}
                        >
                            <EyeIcon off={showPassword} />
                        </button>
                    </span>
                </label>
                {mode === "register" ? (
                    <label htmlFor="confirm" className="flex w-full min-w-0 flex-col gap-1 text-sm font-medium leading-5 text-[#F4F7FF]">
                        Confirm password
                        <input
                            id="confirm"
                            type={showPassword ? "text" : "password"}
                            autoComplete="new-password"
                            value={confirm}
                            onChange={(event) => setConfirm(event.target.value)}
                            className={fieldClass}
                            placeholder="Confirm password"
                            required
                        />
                    </label>
                ) : null}
                {error ? (
                    <p role="alert" className="rounded-2xl bg-[#3b1020] px-3 py-2 text-sm text-[#fda4af]">
                        {error}
                    </p>
                ) : null}
                <button
                    type="submit"
                    disabled={pending}
                    className="flex h-10 w-full min-w-0 shrink-0 items-center justify-center rounded-2xl bg-[#4D7CFF] text-sm font-semibold text-white shadow-[0_10px_24px_rgba(77,124,255,0.45)] transition hover:bg-[#6B93FF] disabled:cursor-wait disabled:opacity-70 sm:h-11 sm:text-base"
                >
                    {pending ? (mode === "login" ? "Signing in…" : "Creating account…") : mode === "login" ? "Sign in" : "Create account"}
                </button>
            </form>
        </div>
    );
}

const fieldClass =
    "box-border h-10 w-full min-w-0 rounded-2xl border border-white/15 bg-[#05070F] px-4 text-base text-white outline-none transition placeholder:text-[#8B97B3] focus:border-[#70E7FF] focus:ring-4 focus:ring-[#4D7CFF]/25";

function ToggleButton({
    active,
    onClick,
    children,
}: {
    active: boolean;
    onClick: () => void;
    children: string;
}) {
    return (
        <button
            type="button"
            aria-pressed={active}
            onClick={onClick}
            className={cx(
                "h-9 rounded-xl text-sm font-semibold transition sm:h-10",
                active ? "bg-[#4D7CFF] text-white shadow-sm" : "text-[#E7EEFF] hover:text-white",
            )}
        >
            {children}
        </button>
    );
}

function switchMode(
    setMode: (mode: Mode) => void,
    setError: (error: string | null) => void,
    mode: Mode,
) {
    setMode(mode);
    setError(null);
}

function validate(mode: Mode, name: string, email: string, password: string, confirm: string): string | null {
    if (mode === "register" && name.trim().length < 2) return "Enter the name FinQon.ai should greet you with.";
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) return "Enter a valid email address.";
    if (mode === "login" && password.length === 0) return "Enter your password.";
    if (mode === "register" && password.length < 10) return "Use at least 10 characters.";
    if (mode === "register" && password.length > 128) return "That password is too long.";
    if (mode === "register" && password !== confirm) return "Those passwords don't match.";
    return null;
}
