"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams, usePathname } from "next/navigation";
import { ActivityFeed } from "@/components/activity/activity-feed";
import { PageHeader } from "@/components/layout/page-header";
import { LoadingState } from "@/components/feedback/loading-state";

export default function ActivityPage() {
  return (
    <Suspense fallback={<LoadingState variant="table" rows={8} />}>
      <ActivityPageContent />
    </Suspense>
  );
}

function ActivityPageContent() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const [eventType, setEventType] = useState(searchParams.get("event") ?? "all");
  const [protocol, setProtocol] = useState(searchParams.get("protocol") ?? "all");
  const [showEphemeral, setShowEphemeral] = useState(searchParams.get("ephemeral") === "1");

  useEffect(() => {
    const params = new URLSearchParams(searchParams);
    if (eventType !== "all") params.set("event", eventType); else params.delete("event");
    if (protocol !== "all") params.set("protocol", protocol); else params.delete("protocol");
    if (showEphemeral) params.set("ephemeral", "1"); else params.delete("ephemeral");
    router.replace(`${pathname}?${params.toString()}`);
  }, [eventType, protocol, showEphemeral, pathname, router, searchParams]);

  return (
    <div className="flex flex-col gap-6 p-4 md:p-8">
      <PageHeader
        title="Activity"
        description="Global operational timeline across all hosts and ports."
      />
      <div className="max-w-4xl">
        <ActivityFeed
          limit={250}
          filterable
          eventType={eventType}
          onEventTypeChange={setEventType}
          protocol={protocol}
          onProtocolChange={setProtocol}
          showEphemeral={showEphemeral}
          onShowEphemeralChange={setShowEphemeral}
        />
      </div>
    </div>
  );
}
