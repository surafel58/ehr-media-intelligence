import { useCallback, useState } from "react"

export type Theme = "light" | "dark"

// Versioned key so the stored value can change shape later without breaking old browsers.
const KEY = "chart-search:theme:v1"

/** The inline script in index.html applies the saved theme before first paint; this hook only reads and toggles it. */
export function useTheme() {
  const [theme, setTheme] = useState<Theme>(() =>
    document.documentElement.classList.contains("dark") ? "dark" : "light",
  )

  const toggle = useCallback(() => {
    setTheme((current) => {
      const next: Theme = current === "dark" ? "light" : "dark"
      document.documentElement.classList.toggle("dark", next === "dark")
      try {
        localStorage.setItem(KEY, next)
      } catch {
        // storage can be blocked; the toggle still works for this visit
      }
      return next
    })
  }, [])

  return { theme, toggle }
}
