import { motion, useMotionValue, useReducedMotion, useSpring } from "framer-motion";
import { useState, type MouseEvent } from "react";

const SLICES = [
    { label: "Dining", amount: "₹8,642", color: "#3B82F6", dash: 78, gap: 174 },
    { label: "Groceries", amount: "₹6,210", color: "#14B8A6", dash: 56, gap: 196 },
    { label: "Fuel", amount: "₹3,480", color: "#F59E0B", dash: 40, gap: 212 },
    { label: "Other", amount: "₹4,120", color: "#8B5CF6", dash: 48, gap: 204 },
] as const;

const OFFSETS = [0, -78, -134, -174];

export function LedgerScene() {
    const reduce = useReducedMotion();
    const [active, setActive] = useState(false);
    const rotateX = useMotionValue(reduce ? 0 : 10);
    const rotateY = useMotionValue(reduce ? 0 : -16);
    const springX = useSpring(rotateX, { stiffness: 70, damping: 18, mass: 0.7 });
    const springY = useSpring(rotateY, { stiffness: 70, damping: 18, mass: 0.7 });

    function onMove(event: MouseEvent<HTMLDivElement>) {
        if (reduce) return;
        const bounds = event.currentTarget.getBoundingClientRect();
        const px = (event.clientX - bounds.left) / bounds.width - 0.5;
        const py = (event.clientY - bounds.top) / bounds.height - 0.5;
        rotateY.set(px * 28 - 8);
        rotateX.set(-py * 18 + 6);
        setActive(true);
    }

    function onLeave() {
        rotateY.set(reduce ? 0 : -16);
        rotateX.set(reduce ? 0 : 10);
        setActive(false);
    }

    return (
        <div
            className="relative mx-auto mt-2 h-[390px] w-full max-w-[640px] [perspective:1400px]"
            onMouseMove={onMove}
            onMouseLeave={onLeave}
            aria-hidden="true"
        >
            <div
                className={`absolute bottom-8 left-1/2 h-10 w-64 -translate-x-1/2 rounded-full bg-[#12263f]/10 blur-xl transition-opacity ${active ? "opacity-80" : "opacity-50"}`}
            />
            <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 [transform-style:preserve-3d]">
                <motion.div
                    style={{ rotateX: springX, rotateY: springY, transformStyle: "preserve-3d" }}
                    animate={reduce ? undefined : { y: [0, -12, 0] }}
                    transition={{ duration: 7, repeat: Infinity, ease: "easeInOut" }}
                >
                    <div
                        className="absolute left-[-150px] top-[28px] h-[200px] w-[250px] rounded-[28px] bg-gradient-to-br from-[#F6D3C4] to-[#F8E7DC] shadow-xl"
                        style={{ transform: "translateZ(-90px) rotateY(-22deg)" }}
                    />
                    <div
                        className="absolute left-[-70px] top-[-48px] h-[300px] w-[280px] rounded-[28px] border border-white/70 bg-white/90 shadow-[0_30px_80px_rgb(18_38_63_/_0.12)] backdrop-blur"
                        style={{ transform: "translateZ(24px) rotateY(-8deg)" }}
                    >
                        <div className="flex items-start justify-between gap-3 px-5 pt-4">
                            <div>
                                <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-muted">
                                    Sample view
                                </p>
                                <p className="mt-1 text-sm font-semibold text-ink">Dining · last month</p>
                            </div>
                            <p className="text-lg font-semibold text-ink">₹8,642</p>
                        </div>
                        <div className="relative mx-auto mt-1 h-[150px] w-[150px]">
                            <svg viewBox="0 0 120 120" className="h-full w-full -rotate-90">
                                <circle cx="60" cy="60" r="40" fill="none" stroke="#EEF2F7" strokeWidth="14" />
                                {SLICES.map((slice, index) => (
                                    <circle
                                        key={slice.label}
                                        cx="60"
                                        cy="60"
                                        r="40"
                                        fill="none"
                                        stroke={slice.color}
                                        strokeWidth="14"
                                        strokeDasharray={`${slice.dash} ${slice.gap}`}
                                        strokeDashoffset={OFFSETS[index] ?? 0}
                                        strokeLinecap="butt"
                                    />
                                ))}
                            </svg>
                            <div className="absolute inset-0 grid place-items-center">
                                <div className="text-center">
                                    <p className="text-[11px] text-muted">Spend</p>
                                    <p className="text-sm font-semibold">4 merchants</p>
                                </div>
                            </div>
                        </div>
                        <ul className="grid grid-cols-2 gap-x-3 gap-y-1.5 px-5 pb-4 text-xs">
                            {SLICES.map((slice) => (
                                <li key={slice.label} className="flex items-center justify-between gap-2">
                                    <span className="flex items-center gap-1.5 text-ink">
                                        <span className="h-2 w-2 rounded-full" style={{ background: slice.color }} />
                                        {slice.label}
                                    </span>
                                    <span className="font-medium text-muted">{slice.amount}</span>
                                </li>
                            ))}
                        </ul>
                    </div>
                    <motion.div
                        className="absolute left-[176px] top-[-8px] grid h-[88px] w-[88px] place-items-center rounded-full bg-gradient-to-br from-[#F6E27A] to-[#E7A93A] text-2xl font-semibold text-[#6A4A12] shadow-[0_16px_40px_rgb(231_169_58_/_0.45)]"
                        style={{ translateZ: 110 }}
                        animate={reduce ? undefined : { rotateY: [0, 180, 360] }}
                        transition={{ duration: 14, repeat: Infinity, ease: "linear" }}
                    >
                        ₹
                    </motion.div>
                    <div
                        className="absolute left-[-188px] top-[78px] w-[156px] rounded-2xl bg-white p-3 shadow-[0_18px_40px_rgb(18_38_63_/_0.12)]"
                        style={{ transform: "translateZ(80px) rotateY(12deg)" }}
                    >
                        <p className="text-[11px] font-semibold text-muted">Top merchants</p>
                        <div className="mt-3 flex h-16 items-end gap-2">
                            {[70, 100, 48, 80, 36].map((height, index) => (
                                <span
                                    key={height}
                                    className="w-full rounded-t-md"
                                    style={{
                                        height: `${height}%`,
                                        background: ["#3B82F6", "#14B8A6", "#F59E0B", "#8B5CF6", "#EC4899"][index],
                                    }}
                                />
                            ))}
                        </div>
                    </div>
                </motion.div>
            </div>
        </div>
    );
}
