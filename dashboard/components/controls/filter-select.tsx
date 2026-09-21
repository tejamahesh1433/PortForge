"use client";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export interface FilterOption {
  value: string;
  label: string;
}

interface FilterSelectProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: FilterOption[];
  /** Value representing "no filter applied". Defaults to "all". */
  allValue?: string;
}

/** One dropdown filter (OS, status, Docker availability, source, ...)
 * used inside a FilterBar. Always includes an explicit "All <label>"
 * option so clearing a filter is a first-class choice, not a hidden
 * empty-string edge case.
 */
export function FilterSelect({ label, value, onChange, options, allValue = "all" }: FilterSelectProps) {
  return (
    <Select value={value} onValueChange={(next) => onChange(next ?? allValue)}>
      <SelectTrigger className="h-8 w-auto min-w-32" aria-label={label}>
        <div className="flex items-center gap-1.5">
          <span className="text-muted-foreground">{label}:</span>
          <SelectValue placeholder="All" />
        </div>
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={allValue}>All {label}</SelectItem>
        {options.map((option) => (
          <SelectItem key={option.value} value={option.value}>
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
