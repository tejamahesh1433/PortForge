import { RotateCcw, X } from "lucide-react";
import { Button } from "@/components/ui/button";

export interface ActiveFilter { label: string; value: string; onRemove: () => void }

export function ActiveFilters({ filters, onReset }: { filters: ActiveFilter[]; onReset: () => void }) {
  if (!filters.length) return null;
  return (
    <div aria-label="Active filters" className="mb-4 flex flex-wrap items-center gap-2">
      <span className="text-xs font-medium text-muted-foreground">Filtered by</span>
      {filters.map((filter) => (
        <button key={`${filter.label}-${filter.value}`} type="button" onClick={filter.onRemove} className="inline-flex items-center gap-1 rounded-full border border-border bg-muted/50 px-2.5 py-1 text-xs hover:bg-muted">
          <span className="text-muted-foreground">{filter.label}:</span> {filter.value}<X className="size-3" aria-hidden="true" />
        </button>
      ))}
      <Button variant="ghost" size="sm" onClick={onReset}><RotateCcw className="size-3.5" aria-hidden="true" />Reset filters</Button>
    </div>
  );
}
