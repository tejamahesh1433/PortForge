import Link from "next/link";
import type { LucideIcon } from "lucide-react";
import { cn } from "cn";
import { Card, CardContent } from "@/components/ui/card";

interface MetricCardProps { label: string; value: number | string; icon: LucideIcon; detail?: string; accent?: "default" | "emerald" | "red" | "blue" | "violet" | "amber"; href?: string }
const ACCENT_CLASSES: Record<NonNullable<MetricCardProps["accent"]>, string> = { default: "bg-muted text-muted-foreground", emerald: "bg-emerald-500/10 text-emerald-400", red: "bg-red-500/10 text-red-400", blue: "bg-blue-500/10 text-blue-400", violet: "bg-violet-500/10 text-violet-400", amber: "bg-amber-500/10 text-amber-400" };

export function MetricCard({ label, value, icon: Icon, detail, accent = "default", href }: MetricCardProps) {
  const card = <Card className="border-border bg-card transition-colors hover:border-primary/35"><CardContent className="flex items-center gap-3 px-4 py-3"><span className={cn("flex size-8 shrink-0 items-center justify-center rounded-lg", ACCENT_CLASSES[accent])}><Icon className="size-4" aria-hidden="true" /></span><div className="min-w-0"><p className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">{label}</p><div className="flex items-baseline gap-2"><p className="text-xl font-semibold tracking-tight text-foreground">{value}</p>{detail ? <p className="truncate text-xs text-muted-foreground">{detail}</p> : null}</div></div></CardContent></Card>;
  return href ? <Link href={href} className="rounded-xl outline-none focus-visible:ring-2 focus-visible:ring-ring">{card}</Link> : card;
}
