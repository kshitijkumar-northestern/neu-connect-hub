import { Outlet } from "react-router";

/** Placeholder shell until the app shell adds the header and responsive layout. */
export function RootLayout() {
  return (
    <main className="mx-auto flex min-h-svh max-w-3xl flex-col justify-center gap-6 px-4 py-16">
      <Outlet />
    </main>
  );
}
