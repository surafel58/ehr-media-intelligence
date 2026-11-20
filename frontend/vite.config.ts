import path from "node:path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

const API = process.env.API_URL ?? "http://127.0.0.1:8000"

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": path.resolve(import.meta.dirname, "./src") },
  },
  server: {
    // During development the FastAPI service runs on its own port.
    proxy: {
      "/search": API,
      "/patients": API,
      "/resources": API,
      "/health": API,
    },
  },
})
