import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useState, type DragEvent } from "react";

import { useAuth } from "@/auth/AuthProvider";
import { Composer } from "@/components/desk/Composer";
import { EvidencePanel } from "@/components/desk/EvidencePanel";
import { HistoryPanel } from "@/components/desk/HistoryPanel";
import { TopBar } from "@/components/desk/TopBar";
import { UploadDialog } from "@/components/desk/UploadDialog";
import { EmptyThread, TurnView } from "@/components/desk/TurnView";
import { useChatStream } from "@/hooks/useChatStream";
import { useUploads } from "@/hooks/useUploads";
import { cx } from "@/lib/cx";
import { greetingName } from "@/lib/name";
import { useMinWidth } from "@/lib/useMinWidth";

export function Desk() {
    const auth = useAuth();
    const chat = useChatStream();
    const historyOpen = useMinWidth(1120);
    const evidenceBeside = useMinWidth(800);
    const uploads = useUploads(chat.refreshStatement);
    const [uploadOpen, setUploadOpen] = useState(false);
    const [dragging, setDragging] = useState(false);
    const [drawer, setDrawer] = useState(false);
    const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
    const email = auth.user?.email ?? "";
    const name = greetingName(auth.user);
    const initial = name.slice(0, 1).toUpperCase() || "F";

    useEffect(() => {
        if (historyOpen) setDrawer(false);
    }, [historyOpen]);

    function openChat(id: string) {
        void chat.openConversation(id);
        setDrawer(false);
    }

    const history = (
        <HistoryPanel
            conversations={chat.conversations}
            activeId={chat.state.conversationId}
            error={chat.historyError}
            collapsed={historyOpen ? sidebarCollapsed : false}
            onToggle={historyOpen ? () => setSidebarCollapsed((open) => !open) : () => setDrawer(false)}
            toggleLabel={historyOpen ? "Collapse sidebar" : "Close chats"}
            onOpen={openChat}
            onNew={() => {
                chat.newChat();
                setDrawer(false);
            }}
            onRename={(id, title) => void chat.renameConversation(id, title)}
            onPin={(id, pinned) => void chat.setPinned(id, pinned)}
            onDelete={(id) => void chat.removeConversation(id)}
        />
    );

    function hasFiles(event: DragEvent) {
        return Array.from(event.dataTransfer.types).includes("Files");
    }

    return (
        <div
            className="relative h-dvh overflow-hidden bg-[#05070F] text-[#F7FAFF]"
            onDragEnter={(event) => {
                if (hasFiles(event)) setDragging(true);
            }}
            onDragOver={(event) => {
                if (hasFiles(event)) event.preventDefault();
            }}
            onDragLeave={(event) => {
                if (event.currentTarget === event.target || !event.relatedTarget) setDragging(false);
            }}
            onDrop={(event) => {
                if (!hasFiles(event)) return;
                event.preventDefault();
                setDragging(false);
                uploads.stage(Array.from(event.dataTransfer.files));
                setUploadOpen(true);
            }}
        >
            <div
                className="pointer-events-none absolute inset-0 bg-[radial-gradient(880px_460px_at_12%_-8%,rgb(77_124_255/0.16),transparent_58%),radial-gradient(640px_420px_at_100%_110%,rgb(112_231_255/0.08),transparent_52%)]"
                aria-hidden="true"
            />
            <div className="relative flex h-full min-h-0 w-full gap-2 overflow-hidden p-2 sm:p-3">
                {historyOpen ? (
                    <aside
                        className={cx(
                            "h-full min-h-0 shrink-0 overflow-hidden transition-[width] duration-300 ease-out",
                            sidebarCollapsed ? "w-[72px]" : "w-[228px]",
                        )}
                    >
                        {history}
                    </aside>
                ) : null}
                <div className="flex min-h-0 min-w-0 flex-1 flex-col">
                    <TopBar
                        statement={chat.statement}
                        statementStatus={chat.statementStatus}
                        name={name}
                        email={email}
                        onLogout={() => void auth.logout()}
                        onOpenHistory={() => setDrawer(true)}
                    />
                    <section className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-3xl border border-white/10 bg-[#0A0E18]/80 shadow-panel backdrop-blur-xl">
                        <div className="no-scrollbar min-h-0 flex-1 overflow-y-auto px-4 py-5 sm:px-6">
                            {chat.state.turns.length === 0 ? (
                                <div className="flex min-h-full items-center justify-center">
                                    <EmptyThread
                                        name={name}
                                        onSuggest={(text) => void chat.send(text)}
                                        onUpload={() => setUploadOpen(true)}
                                        uploadedCount={uploads.readyFiles.length}
                                    />
                                </div>
                            ) : (
                                <div className="space-y-6">
                                    {chat.state.turns.map((turn, index) => (
                                        <TurnView
                                            key={turn.localId}
                                            turn={turn}
                                            isLast={index === chat.state.turns.length - 1}
                                            streaming={chat.state.streaming}
                                            initial={initial}
                                            showEvidenceLink={
                                                !evidenceBeside &&
                                                chat.state.evidence !== null &&
                                                index === chat.state.turns.length - 1
                                            }
                                            onSuggest={(text) => void chat.send(text)}
                                            onRetry={(text) => void chat.send(text)}
                                        />
                                    ))}
                                    {!evidenceBeside ? (
                                        <div id="evidence" className="scroll-mt-4">
                                            <EvidencePanel state={chat.state} onChart={chat.setChart} framed={false} />
                                        </div>
                                    ) : null}
                                </div>
                            )}
                        </div>
                        <Composer
                            disabled={chat.state.streaming}
                            onSend={(text) => void chat.send(text)}
                            files={uploads.readyFiles}
                            onAttach={() => setUploadOpen(true)}
                        />
                    </section>
                </div>
                {evidenceBeside ? (
                    <aside id="evidence" className="h-full min-h-0 w-[clamp(280px,32vw,400px)] shrink-0">
                        <EvidencePanel state={chat.state} onChart={chat.setChart} />
                    </aside>
                ) : null}
            </div>
            <UploadDialog open={uploadOpen} uploads={uploads} onClose={() => setUploadOpen(false)} />
            {dragging && !uploadOpen ? (
                <div
                    className="pointer-events-none fixed inset-0 z-40 grid place-items-center bg-[#05070F]/80 backdrop-blur-sm"
                    aria-hidden="true"
                >
                    <div className="rounded-3xl border-2 border-dashed border-[#70E7FF]/70 bg-[#0A0E18] px-10 py-8 text-center">
                        <p className="text-lg font-semibold">Drop statements to upload</p>
                        <p className="mt-1 text-sm text-white/50">CSV, XLSX or PDF</p>
                    </div>
                </div>
            ) : null}
            <AnimatePresence>
                {drawer ? (
                    <motion.div className="fixed inset-0 z-40" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
                        <button
                            type="button"
                            className="absolute inset-0 bg-[#05070F]/70"
                            aria-label="Close chats"
                            onClick={() => setDrawer(false)}
                        />
                        <motion.div
                            className="absolute inset-y-3 left-3 w-[min(280px,calc(100%-1.5rem))]"
                            initial={{ x: -28 }}
                            animate={{ x: 0 }}
                            exit={{ x: -28 }}
                            transition={{ type: "spring", stiffness: 320, damping: 32 }}
                        >
                            {history}
                        </motion.div>
                    </motion.div>
                ) : null}
            </AnimatePresence>
        </div>
    );
}
