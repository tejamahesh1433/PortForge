"use client";

import { useMemo, useState, type ReactNode } from "react";
import { Check, Copy, Plus, ServerPlus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { toast } from "@/components/ui/toast";
import { useMintEnrollmentToken } from "@/hooks/use-enrollment";
import { PORTFORGE_API_URL } from "@/lib/api/config";

function CopyBlock({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-medium text-muted-foreground">{label}</p>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          aria-label={`Copy ${label}`}
          onClick={() => {
            void navigator.clipboard.writeText(value);
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1200);
          }}
        >
          {copied ? <Check className="size-3.5 text-emerald-400" /> : <Copy className="size-3.5" />}
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
      <pre className="overflow-x-auto rounded-lg border border-border bg-muted/40 p-3 font-mono text-xs whitespace-pre-wrap break-all">
        {value}
      </pre>
    </div>
  );
}

function Step({ n, title, children }: { n: number; title: string; children: ReactNode }) {
  return (
    <div className="space-y-2 rounded-lg border border-border p-3">
      <p className="text-xs font-semibold text-foreground">
        <span className="mr-2 inline-flex size-5 items-center justify-center rounded-full bg-primary text-[10px] text-primary-foreground">
          {n}
        </span>
        {title}
      </p>
      <div className="space-y-2 text-xs text-muted-foreground">{children}</div>
    </div>
  );
}

export function AddHostDialog({
  triggerLabel = "Add host",
  variant = "default",
}: {
  triggerLabel?: string;
  variant?: "default" | "outline";
}) {
  const [open, setOpen] = useState(false);
  const [label, setLabel] = useState("");
  const [ttlHours, setTtlHours] = useState("24");
  const [centralUrl, setCentralUrl] = useState(PORTFORGE_API_URL);
  const [token, setToken] = useState<string | null>(null);
  const [expiresAt, setExpiresAt] = useState<string | null>(null);
  const mutation = useMintEnrollmentToken();

  const server = centralUrl.replace(/\/$/, "");

  const enrollCommand = useMemo(() => {
    if (!token) return "";
    return `portforge agent enroll --server ${server} --token "${token}"`;
  }, [server, token]);

  const installCommands = useMemo(
    () =>
      [
        "git clone https://github.com/tejamahesh1433/PortForge.git",
        "cd PortForge",
        "git checkout v1.1.2",
        "python -m venv .venv",
        "# Windows: .venv\\Scripts\\activate",
        "# macOS / Linux: source .venv/bin/activate",
        "pip install -e ./agent",
        "portforge agent service install",
        "portforge agent service start",
      ].join("\n"),
    [],
  );

  const restartCommands = useMemo(
    () =>
      [
        "portforge agent service stop",
        "portforge agent service start",
        `portforge doctor --url ${server}`,
      ].join("\n"),
    [server],
  );

  const reset = () => {
    setLabel("");
    setTtlHours("24");
    setCentralUrl(PORTFORGE_API_URL);
    setToken(null);
    setExpiresAt(null);
  };

  const mint = () => {
    const ttl = Number(ttlHours);
    mutation.mutate(
      {
        label: label.trim() || undefined,
        ttl_hours: Number.isFinite(ttl) ? ttl : 24,
      },
      {
        onSuccess: (result) => {
          setToken(result.enrollment_token);
          setExpiresAt(result.expires_at);
          toast.add({
            type: "success",
            title: "Enrollment token created",
            description: "Follow the steps below on the new machine. The token is shown only once.",
          });
        },
        onError: (error) =>
          toast.add({
            type: "error",
            title: "Could not create enrollment token",
            description: error instanceof Error ? error.message : "Unknown error",
          }),
      },
    );
  };

  return (
    <>
      <Button
        variant={variant}
        onClick={() => {
          reset();
          setOpen(true);
        }}
      >
        {variant === "default" ? <Plus className="size-4" /> : <ServerPlus className="size-4" />}
        {triggerLabel}
      </Button>
      <Dialog
        open={open}
        onOpenChange={(next) => {
          if (mutation.isPending) return;
          setOpen(next);
          if (!next) reset();
        }}
      >
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>Add host</DialogTitle>
            <DialogDescription>
              {token
                ? "Token created. Run the steps below on the new machine — it will appear in Hosts after it enrolls."
                : "Mint a one-time enrollment token, then install and enroll the agent on the new machine."}
            </DialogDescription>
          </DialogHeader>

          {token ? (
            <div className="space-y-3">
              <label className="block text-xs font-medium text-foreground">
                Central URL (must be reachable from the new machine)
                <Input
                  className="mt-1"
                  value={centralUrl}
                  onChange={(event) => setCentralUrl(event.target.value)}
                  placeholder="http://<CENTRAL_PRIVATE_IP>:58000"
                />
              </label>
              <p className="text-xs text-muted-foreground">
                For another machine use the Central host&apos;s private/LAN/VPN address
                (for example{" "}
                <code className="rounded bg-muted px-1 py-0.5 font-mono">
                  http://&lt;CENTRAL_PRIVATE_IP&gt;:58000
                </code>
                ) — not <code className="rounded bg-muted px-1 py-0.5 font-mono">localhost</code> —
                unless the agent runs on this same computer.
              </p>
              {expiresAt ? (
                <p className="text-xs text-muted-foreground">Token expires at {expiresAt}</p>
              ) : (
                <p className="text-xs text-muted-foreground">This token does not expire.</p>
              )}

              <Step n={1} title="On the new machine — install the agent">
                <p>Needs Python 3.10+ and network access to Central.</p>
                <CopyBlock label="Install commands" value={installCommands} />
              </Step>

              <Step n={2} title="Enroll with this token (one-time)">
                <p>Paste and run exactly once on the new machine:</p>
                <CopyBlock label="Enroll command" value={enrollCommand} />
                <CopyBlock label="Enrollment token only" value={token} />
              </Step>

              <Step n={3} title="Restart the service and verify">
                <CopyBlock label="Restart + doctor" value={restartCommands} />
                <p>
                  Doctor should report Central reachable, credential present, and the service running.
                </p>
              </Step>

              <Step n={4} title="Confirm in this dashboard">
                <p>
                  Refresh the Hosts page. The new machine should show as Healthy within about a minute.
                  If it does not appear, check the Central URL, firewall (outbound to port 58000), and that
                  the token was not already used or expired.
                </p>
              </Step>
            </div>
          ) : (
            <div className="space-y-3">
              <label className="block text-xs font-medium">
                Label (optional)
                <Input
                  className="mt-1"
                  value={label}
                  onChange={(event) => setLabel(event.target.value)}
                  placeholder="macbook, lab-server…"
                />
              </label>
              <label className="block text-xs font-medium">
                Token lifetime (hours)
                <Input
                  className="mt-1"
                  type="number"
                  min={1}
                  max={168}
                  value={ttlHours}
                  onChange={(event) => setTtlHours(event.target.value)}
                />
              </label>
              <p className="text-xs text-muted-foreground">
                After you generate the token, this dialog shows copy-paste install and enroll steps for the
                new machine.
              </p>
            </div>
          )}

          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)} disabled={mutation.isPending}>
              {token ? "Done" : "Cancel"}
            </Button>
            {!token ? (
              <Button onClick={mint} disabled={mutation.isPending}>
                {mutation.isPending ? "Creating…" : "Generate token"}
              </Button>
            ) : null}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
