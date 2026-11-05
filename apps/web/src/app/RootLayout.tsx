import { Outlet } from "react-router";

import { AppShell } from "@/components/layout/AppShell";

export function RootLayout() {
  return (
    <AppShell>
      <Outlet />
    </AppShell>
  );
}
