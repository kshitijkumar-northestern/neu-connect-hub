import { createBrowserRouter } from "react-router";
import { RouterProvider } from "react-router/dom";

import { ThemeProvider } from "@/app/providers/ThemeProvider";
import { routes } from "@/app/routes";
import { AuthProvider } from "@/features/auth/AuthProvider";

const router = createBrowserRouter(routes);

export function App() {
  return (
    <ThemeProvider>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </ThemeProvider>
  );
}
