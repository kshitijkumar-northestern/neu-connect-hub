import { render } from "@testing-library/react";
import { StrictMode } from "react";
import { createMemoryRouter, type RouteObject } from "react-router";
import { RouterProvider } from "react-router/dom";

import { ThemeProvider } from "@/app/providers/ThemeProvider";
import { routes } from "@/app/routes";
import { AuthProvider } from "@/features/auth/AuthProvider";

/** The whole app at `path`, with the same providers as main.tsx/App.tsx (StrictMode included,
 * so effects run twice as in development) and an in-memory router. */
export function renderApp(path: string, routeTable: RouteObject[] = routes) {
  const router = createMemoryRouter(routeTable, { initialEntries: [path] });
  const view = render(
    <StrictMode>
      <ThemeProvider>
        <AuthProvider>
          <RouterProvider router={router} />
        </AuthProvider>
      </ThemeProvider>
    </StrictMode>,
  );
  return { router, ...view };
}
