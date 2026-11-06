import type { RouteObject } from "react-router";

import { HomePage } from "@/app/HomePage";
import { NotFoundPage } from "@/app/NotFoundPage";
import { RootLayout } from "@/app/RootLayout";
import { RouteErrorView } from "@/app/RouteErrorView";

export const routes: RouteObject[] = [
  {
    path: "/",
    element: <RootLayout />,
    errorElement: <RouteErrorView />,
    children: [
      { index: true, element: <HomePage /> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
];
