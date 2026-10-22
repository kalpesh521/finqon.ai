import {
    createContext,
    useCallback,
    useContext,
    useEffect,
    useMemo,
    useRef,
    useState,
    type ReactNode,
} from "react";

import type { TokenPair, UserResponse } from "@/api/eventSchema";
import {
    ApiError,
    apiUrl,
    currentUser,
    loginRequest,
    logoutRequest,
    refreshRequest,
    registerRequest,
    type AuthorizedFetch,
} from "@/api/http";
import { clearStoredAuth, readStoredAuth, writeStoredAuth } from "@/auth/storage";

type Tokens = {
    accessToken: string;
    refreshToken: string;
    expiresAt: number;
    persistent: boolean;
};

type AuthStatus = "loading" | "anonymous" | "authenticated";

type AuthContextValue = {
    status: AuthStatus;
    user: UserResponse | null;
    persistent: boolean;
    login: (email: string, password: string, persistent: boolean) => Promise<void>;
    register: (name: string, email: string, password: string, persistent: boolean) => Promise<void>;
    logout: () => Promise<void>;
    authorizedFetch: AuthorizedFetch;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
    const [status, setStatus] = useState<AuthStatus>("loading");
    const [user, setUser] = useState<UserResponse | null>(null);
    const [persistent, setPersistent] = useState(true);
    const tokensRef = useRef<Tokens | null>(null);
    const refreshFlight = useRef<Promise<string | null> | null>(null);

    const applyPair = useCallback((pair: TokenPair, keep: boolean, nextUser: UserResponse) => {
        tokensRef.current = {
            accessToken: pair.access_token,
            refreshToken: pair.refresh_token,
            expiresAt: Date.now() + pair.expires_in * 1000,
            persistent: keep,
        };
        writeStoredAuth({ refreshToken: pair.refresh_token, persistent: keep });
        setPersistent(keep);
        setUser(nextUser);
        setStatus("authenticated");
    }, []);

    const refresh = useCallback(async (): Promise<string | null> => {
        const existing = refreshFlight.current;
        if (existing) return existing;
        const task = (async () => {
            const stored = readStoredAuth();
            const refreshToken = tokensRef.current?.refreshToken ?? stored?.refreshToken;
            const keep = tokensRef.current?.persistent ?? stored?.persistent ?? true;
            if (!refreshToken) return null;
            try {
                const pair = await refreshRequest(refreshToken);
                const nextUser = await currentUser(pair.access_token);
                applyPair(pair, keep, nextUser);
                return pair.access_token;
            } catch {
                tokensRef.current = null;
                clearStoredAuth();
                setUser(null);
                setStatus("anonymous");
                return null;
            }
        })();
        refreshFlight.current = task;
        try {
            return await task;
        } finally {
            refreshFlight.current = null;
        }
    }, [applyPair]);

    const authorizedFetch = useCallback<AuthorizedFetch>(
        async (path, init) => {
            const current = tokensRef.current;
            let token: string | null;
            if (!current || Date.now() > current.expiresAt - 30_000) {
                token = await refresh();
            } else {
                token = current.accessToken;
            }
            if (!token) throw new ApiError(401, "unauthorized", "Sign in again.");
            const headers = new Headers(init?.headers);
            headers.set("Authorization", `Bearer ${token}`);
            const response = await fetch(apiUrl(path), { ...init, headers });
            if (response.status !== 401) return response;
            const rotated = await refresh();
            if (!rotated) return response;
            headers.set("Authorization", `Bearer ${rotated}`);
            return fetch(apiUrl(path), { ...init, headers });
        },
        [refresh],
    );

    useEffect(() => {
        const stored = readStoredAuth();
        if (!stored) {
            setStatus("anonymous");
            return;
        }
        setPersistent(stored.persistent);
        void refresh();
    }, [refresh]);

    const login = useCallback(
        async (email: string, password: string, keep: boolean) => {
            const pair = await loginRequest(email.trim(), password);
            const nextUser = await currentUser(pair.access_token);
            applyPair(pair, keep, nextUser);
        },
        [applyPair],
    );

    const register = useCallback(
        async (name: string, email: string, password: string, keep: boolean) => {
            await registerRequest(name.trim(), email.trim(), password);
            await login(email, password, keep);
        },
        [login],
    );

    const logout = useCallback(async () => {
        const refreshToken = tokensRef.current?.refreshToken ?? readStoredAuth()?.refreshToken;
        tokensRef.current = null;
        clearStoredAuth();
        setUser(null);
        setStatus("anonymous");
        if (!refreshToken) return;
        try {
            await logoutRequest(refreshToken);
        } catch {
            // The browser session is already gone. A failed revoke must not keep the desk open.
        }
    }, []);

    const value = useMemo<AuthContextValue>(
        () => ({ status, user, persistent, login, register, logout, authorizedFetch }),
        [status, user, persistent, login, register, logout, authorizedFetch],
    );

    return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
    const value = useContext(AuthContext);
    if (!value) throw new Error("useAuth must be used within AuthProvider");
    return value;
}
