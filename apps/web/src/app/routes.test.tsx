import { render, screen } from "@testing-library/react";
import { createMemoryRouter, type RouteObject } from "react-router";
import { RouterProvider } from "react-router/dom";
import { describe, expect, it, vi } from "vitest";

import { ThemeProvider } from "@/app/providers/ThemeProvider";
import { routes } from "@/app/routes";

function renderAt(path: string, routeTable: RouteObject[] = routes) {
  const router = createMemoryRouter(routeTable, { initialEntries: [path] });
  return render(
    <ThemeProvider>
      <RouterProvider router={router} />
    </ThemeProvider>,
  );
}

describe("routes", () => {
  it("renders the home page at /", async () => {
    renderAt("/");

    expect(await screen.findByRole("heading", { level: 1, name: "NU Connect Hub" })).toBeInTheDocument();
  });

  it("renders a not-found page with a way home for unknown paths", async () => {
    renderAt("/does/not/exist");

    expect(await screen.findByRole("heading", { level: 1, name: "Page not found" })).toBeInTheDocument();
    const homeLink = screen.getByRole("link", { name: "Go to the home page" });
    expect(homeLink).toHaveAttribute("href", "/");
    // Rendered through the shadcn Button primitive, proving the @/components/ui alias resolves.
    expect(homeLink).toHaveAttribute("data-slot", "button");
  });

  it("shows a generic error view without leaking the error message", async () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    function Boom(): never {
      throw new Error("secret internal detail");
    }
    const [root] = routes;
    if (!root) throw new Error("routes must define a root route");
    // Same layout and error boundary as the real root route, with a child that throws.
    const table: RouteObject[] = [
      {
        path: "/",
        element: root.element,
        errorElement: root.errorElement,
        children: [{ path: "boom", element: <Boom /> }],
      },
    ];

    renderAt("/boom", table);

    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong");
    expect(screen.queryByText(/secret internal detail/)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to the home page" })).toHaveAttribute("href", "/");
  });
});
