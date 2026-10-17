import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The agent serves the API; Vite serves the app and forwards /api, keeping one origin.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8765" } },
});
