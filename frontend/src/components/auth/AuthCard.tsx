import { motion, useMotionValue, useReducedMotion, useSpring } from "framer-motion";
import { useState, type FormEvent, type MouseEvent } from "react";

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
    const reduce = useReducedMotion();
    const rotateX = useMotionValue(0);
    const rotateY = useMotionValue(0);
    const springX = useSpring(rotateX, { stiffness: 140, damping: 18 });
    const springY = useSpring(rotateY, { stiffness: 140, damping: 18 });

    function onTilt(event: MouseEvent<HTMLDivElement>) {
        if (reduce) return;
        const bounds = event.currentTarget.getBoundingClientRect();
        const px = (event.clientX - bounds.left) / bounds.width - 0.5;
        const py = (event.clientY - bounds.top) / bounds.height - 0.5;
        rotateY.set(px * 6);
        rotateX.set(-py * 4);
    }

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
            setError(caught instanceof ApiError ? caught.message : "Could not reach Finqon.ai. Try again.");
        } finally {
            setPending(false);
        }
    }

    return (
        <motion.div
            onMouseMove={onTilt}
            onMouseLeave={() => {
                rotateX.set(0);
                rotateY.set(0);
            }}
            style={{ rotateX: springX, rotateY: springY, transformStyle: "preserve-3d" }}
            className="w-full max-w-[440px] rounded-[28px] border border-white/10 bg-[#0A0E18] px-5 py-4 text-[#F4F7FF] shadow-[0_18px_50px_rgba(0,0,0,0.35)] sm:px-6 [@media(min-height:860px)]:px-8 [@media(min-height:860px)]:py-6"
        >
            <div className="grid grid-cols-2 rounded-2xl bg-[#05070F] p-1" role="group" aria-label="Sign in or register">
                <ToggleButton active={mode === "login"} onClick={() => switchMode(setMode, setError, "login")}>
                    Sign in
                </ToggleButton>
                <ToggleButton active={mode === "register"} onClick={() => switchMode(setMode, setError, "register")}>
                    Register
                </ToggleButton>
            </div>
            <div className="mt-4 [@media(min-height:860px)]:mt-5">
                <h2 className={cx("min-h-7 text-xl font-semibold tracking-tight [@media(min-height:860px)]:text-2xl", align === "center" && "text-center")}>
                    {mode === "login" ? "Welcome back" : "Create your account"}
                </h2>
            </div>
            <form className="mt-4 space-y-3 [@media(min-height:860px)]:mt-5 [@media(min-height:860px)]:space-y-4" onSubmit={(event) => void onSubmit(event)} noValidate>
                {mode === "register" ? (
                    <label className="block text-sm font-medium text-[#F4F7FF]" htmlFor="name">
                        Name
                        <input
                            id="name"
                            type="text"
                            autoComplete="name"
                            value={name}
                            onChange={(event) => setName(event.target.value)}
                            className={`${fieldClass} mt-1`}
                            placeholder="Kalpesh Pawar"
                            required
                        />
                    </label>
                ) : null}
                <label className="block text-sm font-medium text-[#F4F7FF]" htmlFor="email">
                    Email
                    <input
                        id="email"
                        type="email"
                        autoComplete="email"
                        value={email}
                        onChange={(event) => setEmail(event.target.value)}
                        className={`${fieldClass} mt-1`}
                        placeholder="you@example.com"
                        required
                    />
                </label>
                <label className="block text-sm font-medium text-[#F4F7FF]" htmlFor="password">
                    Password
                    <span className="relative mt-1 block">
                        <input
                            id="password"
                            type={showPassword ? "text" : "password"}
                            autoComplete={mode === "login" ? "current-password" : "new-password"}
                            value={password}
                            onChange={(event) => setPassword(event.target.value)}
                            className={`${fieldClass} pr-12`}
                            placeholder={mode === "register" ? "At least 10 characters" : "Your password"}
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
                    <label htmlFor="confirm" className="block text-sm font-medium text-[#F4F7FF]">
                        Confirm password
                        <input
                            id="confirm"
                            type={showPassword ? "text" : "password"}
                            autoComplete="new-password"
                            value={confirm}
                            onChange={(event) => setConfirm(event.target.value)}
                            className={`${fieldClass} mt-1`}
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
                    className="mt-1 flex h-11 w-full items-center justify-center rounded-2xl bg-[#4D7CFF] text-base font-semibold text-white shadow-[0_10px_24px_rgba(77,124,255,0.45)] transition hover:bg-[#6B93FF] disabled:cursor-wait disabled:opacity-70 [@media(min-height:860px)]:h-12"
                >
                    {pending ? (mode === "login" ? "Signing in…" : "Creating account…") : mode === "login" ? "Sign in" : "Create account"}
                </button>
            </form>
        </motion.div>
    );
}

const fieldClass =
    "h-11 w-full rounded-2xl border border-white/15 bg-[#05070F] px-4 text-base text-white outline-none transition placeholder:text-[#C5D0E6] focus:border-[#70E7FF] focus:ring-4 focus:ring-[#4D7CFF]/25 md:h-10 [@media(min-height:860px)]:h-12";

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
                "h-11 rounded-xl text-sm font-semibold transition",
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
    if (mode === "register" && name.trim().length < 2) return "Enter the name Finqon.ai should greet you with.";
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) return "Enter a valid email address.";
    if (mode === "login" && password.length === 0) return "Enter your password.";
    if (mode === "register" && password.length < 10) return "Use at least 10 characters.";
    if (mode === "register" && password.length > 128) return "That password is too long.";
    if (mode === "register" && password !== confirm) return "Those passwords don't match.";
    return null;
}
