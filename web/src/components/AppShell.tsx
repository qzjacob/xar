import type { ReactNode } from "react";

/**
 * Pure layout frame: navy Sidebar (left, full height) | column of TopBar (fixed)
 * + scrollable main | DecisionRail (right, own scroll). All content is injected
 * as slots so this stays purely structural.
 */
export function AppShell({
  sidebar,
  topbar,
  rail,
  children,
}: {
  sidebar: ReactNode;
  topbar: ReactNode;
  rail: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="flex h-full w-full overflow-hidden bg-canvas text-brand-900">
      {sidebar}
      <div className="flex min-w-0 flex-1 flex-col">
        {topbar}
        <div className="flex min-h-0 flex-1">
          <main className="scroll-thin min-w-0 flex-1 overflow-y-auto px-5 py-5">{children}</main>
          {rail}
        </div>
      </div>
    </div>
  );
}
