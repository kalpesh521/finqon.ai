import { lazy, Suspense, useState } from "react";
import { motion } from "framer-motion";

import { AuthCard } from "@/components/auth/AuthCard";
import { Mark, Wordmark } from "@/components/icons";

const FinanceUniverse = lazy(() => import("@/components/auth/FinanceUniverse"));

type View = "landing" | "auth";

export function AuthScreen() {
    const [view, setView] = useState<View>("landing");

    return (
        <div className={view === "landing" ? "relative min-h-dvh bg-[#05070F] text-[#F7FAFF] lg:h-dvh lg:overflow-hidden" : "relative h-svh overflow-x-hidden overflow-y-auto bg-[#05070F] text-[#F7FAFF]"}>
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
                    <h1 className="mb-[30px] text-[2rem] font-semibold leading-tight tracking-tight md:text-4xl lg:text-5xl">
                        Ask your bank statement.
                        <span className="mt-2 block bg-gradient-to-r from-[#F7FAFF] to-[#70E7FF] bg-clip-text text-transparent">
                            See the proof.
                        </span>
                    </h1>
                    <p className="mt-4 max-w-sm text-base leading-7 text-[#D5DEF2] md:mt-5">
                        Ask spending questions in plain language.{" "}
                        <span className="font-medium text-[#70E7FF]">FinQon.ai</span> answers from your transactions and shows the SQL, chart, and rows.
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
                        Analyze my statement
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
        <main className="relative flex min-h-full w-full flex-col items-center justify-center px-4 pb-8 pt-14 sm:px-6 sm:pb-10 sm:pt-16">
            <div
                className="pointer-events-none absolute inset-0"
                style={{
                    background:
                        "radial-gradient(ellipse 70% 45% at 50% 0%, rgba(77,124,255,0.28), transparent 60%), radial-gradient(ellipse 40% 30% at 50% 18%, rgba(112,231,255,0.12), transparent 70%)",
                }}
            />
            <div className="relative flex w-full min-w-0 max-w-[400px] flex-col items-center">
                <h1 className="w-full px-1 text-center text-base font-semibold leading-snug text-balance sm:text-lg">
                    <span className="bg-gradient-to-r from-[#F7FAFF] via-[#9FD4FF] to-[#70E7FF] bg-clip-text text-transparent">
                        Continue your statement analysis.
                    </span>
                    <span className="mt-0.5 block text-[#70E7FF]">Answers with proof.</span>
                </h1>
                <div className="mt-3 w-full min-w-0 sm:mt-4">
                    <AuthCard align="center" />
                </div>
            </div>
        </main>
    );
}
