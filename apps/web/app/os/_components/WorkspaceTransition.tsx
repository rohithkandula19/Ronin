"use client";

import { usePathname } from "next/navigation";

/**
 * Re-keys the center workspace on route change so the active surface animates
 * in (rise + fade). A lightweight, dependency-free view transition — reduced
 * motion is handled in CSS (.ws-enter disabled).
 */
export function WorkspaceTransition({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  return (
    <div key={pathname} className="ws-enter h-full">
      {children}
    </div>
  );
}
