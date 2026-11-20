import { CalendarIcon, SearchIcon, XIcon } from "lucide-react"
import { Suspense, lazy, useEffect, useRef } from "react"
import type { DateRange } from "react-day-picker"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import type { ResourceType } from "@/lib/api"
import { formatDate, toIsoDate } from "@/lib/format"

const loadCalendar = () => import("./date-range-calendar")
const DateRangeCalendar = lazy(loadCalendar)

const TYPE_OPTIONS: { value: ResourceType | "all"; label: string }[] = [
  { value: "all", label: "All records" },
  { value: "DocumentReference", label: "Clinical notes" },
  { value: "DiagnosticReport", label: "Lab and imaging reports" },
  { value: "Patient", label: "AI summaries" },
]

interface Props {
  query: string
  onQueryChange: (value: string) => void
  resourceType: ResourceType | null
  onResourceTypeChange: (value: ResourceType | null) => void
  range: DateRange | undefined
  onRangeChange: (value: DateRange | undefined) => void
  busy: boolean
}

export function SearchControls({
  query,
  onQueryChange,
  resourceType,
  onResourceTypeChange,
  range,
  onRangeChange,
  busy,
}: Props) {
  const inputRef = useRef<HTMLInputElement>(null)

  // "/" jumps to the search field from anywhere, like most search tools.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement
      const typing = target.closest("input, textarea, [contenteditable=true], [role=dialog]")
      if (event.key === "/" && !typing) {
        event.preventDefault()
        inputRef.current?.focus()
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [])

  const hasFilters = resourceType !== null || range?.from !== undefined
  const rangeLabel = range?.from
    ? `${formatDate(toIsoDate(range.from))} to ${range.to ? formatDate(toIsoDate(range.to)) : "now"}`
    : "Any date"

  return (
    <section aria-label="Search" className="flex flex-col gap-3">
      <form role="search" onSubmit={(e) => e.preventDefault()} className="relative">
        <label htmlFor="record-search" className="sr-only">
          Search patient records
        </label>
        <SearchIcon
          aria-hidden="true"
          className="pointer-events-none absolute top-1/2 left-4 size-5 -translate-y-1/2 text-muted-foreground"
        />
        <Input
          ref={inputRef}
          id="record-search"
          type="search"
          autoComplete="off"
          spellCheck={false}
          autoFocus
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape" && query) {
              e.preventDefault()
              onQueryChange("")
            }
            if (e.key === "ArrowDown") {
              const first = document.querySelector<HTMLElement>("[data-result-index='0']")
              if (first) {
                e.preventDefault()
                first.focus()
              }
            }
          }}
          placeholder="Search notes, lab and imaging reports, and AI summaries"
          aria-describedby="search-hint"
          aria-busy={busy}
          className="h-14 rounded-xl bg-card pr-12 pl-12 text-[17px] shadow-xs md:text-[17px] [&::-webkit-search-cancel-button]:hidden"
        />
        {query ? (
          <Button
            type="button"
            variant="ghost"
            size="icon-lg"
            aria-label="Clear search"
            onClick={() => {
              onQueryChange("")
              inputRef.current?.focus()
            }}
            className="absolute top-1/2 right-2.5 -translate-y-1/2"
          >
            <XIcon />
          </Button>
        ) : (
          <kbd
            aria-hidden="true"
            className="absolute top-1/2 right-4 hidden -translate-y-1/2 rounded border bg-muted px-1.5 text-xs text-muted-foreground sm:block"
          >
            /
          </kbd>
        )}
        <p id="search-hint" className="sr-only">
          Results update as you type. Press the down arrow to move into the results.
        </p>
      </form>

      <div className="flex flex-wrap items-center gap-2">
        <Select
          value={resourceType ?? "all"}
          onValueChange={(value) => onResourceTypeChange(value === "all" ? null : (value as ResourceType))}
        >
          <SelectTrigger aria-label="Record type" className="h-9 min-w-48 bg-card">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              {TYPE_OPTIONS.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectGroup>
          </SelectContent>
        </Select>

        <Popover>
          <PopoverTrigger asChild>
            <Button
              variant="outline"
              size="lg"
              aria-label={`Date range: ${rangeLabel}`}
              className="bg-card font-normal"
              onMouseEnter={loadCalendar}
              onFocus={loadCalendar}
            >
              <CalendarIcon data-icon="inline-start" />
              {rangeLabel}
            </Button>
          </PopoverTrigger>
          <PopoverContent className="w-auto p-0" align="start">
            <Suspense fallback={<Skeleton className="m-3 h-72 w-64" />}>
              <DateRangeCalendar range={range} onChange={onRangeChange} />
            </Suspense>
            <div className="flex justify-end border-t p-2">
              <Button variant="ghost" size="sm" onClick={() => onRangeChange(undefined)} disabled={!range?.from}>
                Clear dates
              </Button>
            </div>
          </PopoverContent>
        </Popover>

        {hasFilters ? (
          <Button
            variant="ghost"
            size="lg"
            onClick={() => {
              onResourceTypeChange(null)
              onRangeChange(undefined)
            }}
          >
            Clear filters
          </Button>
        ) : null}
      </div>
    </section>
  )
}
