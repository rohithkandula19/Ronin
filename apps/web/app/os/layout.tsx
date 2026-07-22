import type { Metadata } from "next";
import { AppShell } from "./_components/AppShell";

export const metadata: Metadata = {
  title: "Ronin OS",
  description:
    "The Ronin AI operating system — Home, worlds, agents, memory, and approvals in one calm workspace.",
};

export default function OsLayout({ children }: { children: React.ReactNode }) {
  return <AppShell>{children}</AppShell>;
}
