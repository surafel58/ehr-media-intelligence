import { MoonIcon, SunIcon } from "lucide-react"
import useSWR from "swr"

import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { useTheme } from "@/hooks/use-theme"
import { getJson } from "@/lib/api"

interface Health {
  indexed_chunks: number
  embedding_model: string
}

export function AppHeader() {
  const { theme, toggle } = useTheme()
  const { data: health, error } = useSWR<Health>("/health", getJson, { revalidateOnFocus: false })
  const label = theme === "dark" ? "Switch to light theme" : "Switch to dark theme"

  return (
    <header className="border-b bg-card">
      <div className="mx-auto flex h-14 max-w-5xl items-center justify-between gap-4 px-4 sm:px-6">
        <a href="/" className="flex items-center gap-2.5 rounded-md font-semibold" aria-label="Chart Search home">
          <img src="/favicon.svg" alt="" className="size-7" />
          <span className="text-[17px] tracking-tight">Chart Search</span>
        </a>
        <div className="flex items-center gap-3">
          <p className="hidden text-sm text-muted-foreground sm:block" aria-live="polite">
            {error ? "Search service offline" : health ? `${health.indexed_chunks.toLocaleString()} text passages indexed` : "Connecting"}
          </p>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button variant="ghost" size="icon-lg" onClick={toggle} aria-label={label}>
                {theme === "dark" ? <SunIcon /> : <MoonIcon />}
              </Button>
            </TooltipTrigger>
            <TooltipContent>{label}</TooltipContent>
          </Tooltip>
        </div>
      </div>
    </header>
  )
}
