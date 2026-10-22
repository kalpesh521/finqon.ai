import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
    plugins: [react(), tailwindcss()],
    resolve: {
        alias: {
            "@": fileURLToPath(new URL("./src", import.meta.url)),
        },
    },
    server: {
        port: 5173,
        proxy: {
            "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
            "/health": { target: "http://127.0.0.1:8000", changeOrigin: true },
        },
    },
    test: {
        environment: "node",
        include: ["src/**/*.test.ts"],
    },
});
