import Link from "next/link";
import { ChevronRight } from "lucide-react";
import type { ReactNode } from "react";

interface Breadcrumb { label: string; href?: string }
interface PageHeaderProps { title: string; description?: ReactNode; actions?: ReactNode; breadcrumbs?: Breadcrumb[]; status?: ReactNode }

export function PageHeader({ title, description, actions, breadcrumbs, status }: PageHeaderProps) {
  return (
    <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
      <div>
        {breadcrumbs?.length ? (
          <nav aria-label="Breadcrumb" className="mb-2 flex items-center gap-1 text-xs text-muted-foreground">
            {breadcrumbs.map((item, index) => <span key={`${item.label}-${index}`} className="inline-flex items-center gap-1">{index > 0 ? <ChevronRight className="size-3" aria-hidden="true" /> : null}{item.href ? <Link href={item.href} className="hover:text-foreground hover:underline">{item.label}</Link> : <span aria-current="page">{item.label}</span>}</span>)}
          </nav>
        ) : null}
        <h2 className="text-xl font-semibold tracking-tight text-foreground">{title}</h2>
        {description ? <div className="mt-1 text-sm text-muted-foreground">{description}</div> : null}
        {status ? <div className="mt-2">{status}</div> : null}
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </div>
  );
}
