import { AuthProvider, useAuth } from "@/auth/AuthProvider";
import { AuthScreen } from "@/components/auth/AuthScreen";
import { Mark } from "@/components/icons";
import { Desk } from "@/components/desk/Desk";

export function App() {
    return (
        <AuthProvider>
            <Gate />
        </AuthProvider>
    );
}

function Gate() {
    const auth = useAuth();
    if (auth.status === "loading") {
        return (
            <div className="grid h-dvh place-items-center bg-canvas">
                <div className="flex items-center gap-3 text-sm text-muted">
                    <Mark />
                    Opening FinQon.ai…
                </div>
            </div>
        );
    }
    if (auth.status === "anonymous") return <AuthScreen />;
    return <Desk />;
}
