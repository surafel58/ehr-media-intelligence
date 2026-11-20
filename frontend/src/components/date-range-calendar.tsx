import type { DateRange } from "react-day-picker"

import { Calendar } from "@/components/ui/calendar"

// Loaded on demand: the calendar and its date library are only needed once the picker opens.
export default function DateRangeCalendar({
  range,
  onChange,
}: {
  range: DateRange | undefined
  onChange: (value: DateRange | undefined) => void
}) {
  return (
    <Calendar
      mode="range"
      selected={range}
      onSelect={onChange}
      captionLayout="dropdown"
      startMonth={new Date(1990, 0)}
      endMonth={new Date(2030, 11)}
      defaultMonth={range?.from ?? new Date(2024, 0)}
      numberOfMonths={1}
    />
  )
}
