import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { AppShell } from "@/components/layout/app-shell";
import { QueryProvider } from "@/components/providers/query-provider";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Toaster } from "@/components/ui/toast";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "PortForge",
  description: "Port discovery, reservation, and conflict-detection console for PortForge-managed hosts.",
};

export const dynamic = "force-dynamic";
export const fetchCache = "force-no-store";

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`dark ${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full">
        <QueryProvider>
          <TooltipProvider>
            <Toaster>
              <AppShell>{children}</AppShell>
            </Toaster>
          </TooltipProvider>
        </QueryProvider>
      </body>
    </html>
  );
}
