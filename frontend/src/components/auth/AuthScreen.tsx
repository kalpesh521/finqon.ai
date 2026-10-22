import { lazy, Suspense, useState } from "react";
import { motion } from "framer-motion";

import { AuthCard } from "@/components/auth/AuthCard";
import { Mark, Wordmark } from "@/components/icons";

const FinanceUniverse = lazy(() => import("@/components/auth/FinanceUniverse"));

type View = "landing" | "auth";

export function AuthScreen() {
    const [view, setView] = useState<View>("landing");

    return (
        <div className="relative min-h-dvh bg-[#05070F] text-[#F7FAFF] lg:h-dvh lg:overflow-hidden">
            <button
                type="button"
                className="absolute left-5 top-5 z-20 flex items-center gap-3 sm:left-8 sm:top-6"
                onClick={() => setView("landing")}
            >
                <Mark className="h-9 w-9" />
                <Wordmark className="text-lg font-semibold tracking-wide" />
            </button>

            {view === "landing" ? <Landing onSignIn={() => setView("auth")} /> : <SignInStage />}
        </div>
    );
}

function Landing({ onSignIn }: { onSignIn: () => void }) {
    return (
        <main className="grid min-h-dvh bg-[#05070F] md:h-full md:grid-cols-[minmax(280px,42%)_1fr] md:overflow-hidden">
            <section className="relative flex flex-col justify-center overflow-hidden bg-[#05070F] px-5 pb-6 pt-20 sm:px-8 md:px-8 md:pt-24 lg:px-14">
                <div
                    className="pointer-events-none absolute left-10 top-1/2 h-56 w-56 -translate-y-1/2 rounded-full bg-[#4D7CFF]/15 blur-3xl"
                    aria-hidden="true"
                />
                <motion.div
                    className="relative max-w-md"
                    initial={{ opacity: 0, y: 16 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.55, ease: "easeOut" }}
                >
                    <h1 className="text-[2rem] font-semibold leading-tight tracking-tight md:text-4xl lg:text-5xl">
                        Ask your statement.
                        <span className="mt-2 block bg-gradient-to-r from-[#F7FAFF] to-[#70E7FF] bg-clip-text text-transparent">
                            See the proof.
                        </span>
                    </h1>
                    <p className="mt-4 max-w-sm text-base leading-7 text-[#D5DEF2] md:mt-5">
                        Ask in plain language.{" "}
                        <span className="font-medium text-[#70E7FF]">Finqon.ai</span> answers from your transactions and shows the rows.
                    </p>
                    <motion.button
                        type="button"
                        className="group mt-8 inline-flex h-12 items-center gap-3 rounded-full bg-[#4D7CFF] px-7 text-base font-semibold text-white shadow-[0_12px_32px_rgba(77,124,255,0.45)]"
                        onClick={onSignIn}
                        whileHover={{ scale: 1.04 }}
                        whileTap={{ scale: 0.98 }}
                        animate={{ boxShadow: ["0 12px 32px rgba(77,124,255,0.35)", "0 12px 36px rgba(112,231,255,0.45)", "0 12px 32px rgba(77,124,255,0.35)"] }}
                        transition={{ boxShadow: { duration: 2.4, repeat: Infinity, ease: "easeInOut" }, scale: { duration: 0.2 } }}
                    >
                        Get started
                        <span className="transition group-hover:translate-x-1" aria-hidden="true">
                            →
                        </span>
                    </motion.button>
                </motion.div>
            </section>
            <section className="relative h-[52vh] min-h-[280px] bg-[#05070F] md:h-auto md:min-h-0">
                <div
                    className="pointer-events-none absolute left-1/2 top-1/2 h-72 w-72 -translate-x-1/2 -translate-y-1/2 rounded-full bg-[#4D7CFF]/15 blur-3xl"
                    aria-hidden="true"
                />
                <Suspense fallback={<div className="h-full min-h-[420px] bg-[#05070F]" />}>
                    <FinanceUniverse />
                </Suspense>
            </section>
        </main>
    );
}

function SignInStage() {
    return (
        <main className="relative flex min-h-dvh flex-col items-center overflow-x-hidden overflow-y-auto px-4 pb-6 pt-20 sm:px-6 md:justify-center md:overflow-hidden md:pt-16">
            <div
                className="pointer-events-none absolute inset-0"
                style={{
                    background:
                        "radial-gradient(ellipse 70% 45% at 50% 0%, rgba(77,124,255,0.28), transparent 60%), radial-gradient(ellipse 40% 30% at 50% 18%, rgba(112,231,255,0.12), transparent 70%)",
                }}
            />
            <div className="relative flex w-full max-w-[420px] flex-col items-center">
                <h1 className="text-center text-2xl font-semibold leading-tight tracking-tight [@media(min-height:820px)]:text-4xl">
                    <span className="bg-gradient-to-r from-[#F7FAFF] via-[#9FD4FF] to-[#70E7FF] bg-clip-text text-transparent">
                        Ask your statement.
                    </span>
                    <span className="mt-1 block text-[#70E7FF]">See the proof.</span>
                </h1>
                <div className="mt-4 w-full [@media(min-height:820px)]:mt-8">
                    <AuthCard align="center" />
                </div>
            </div>
        </main>
    );
}
