import type { RouteObject } from "react-router";

import { HomePage } from "@/app/HomePage";
import { NotFoundPage } from "@/app/NotFoundPage";
import { RootLayout } from "@/app/RootLayout";
import { RouteErrorView } from "@/app/RouteErrorView";
import { ForgotPasswordPage } from "@/features/auth/pages/ForgotPasswordPage";
import { RegisterPage } from "@/features/auth/pages/RegisterPage";
import { ResetPasswordPage } from "@/features/auth/pages/ResetPasswordPage";
import { SignInPage } from "@/features/auth/pages/SignInPage";
import { VerifyEmailPage } from "@/features/auth/pages/VerifyEmailPage";
import { RequireAuth, RequireVerified } from "@/features/auth/guards";
import { ConnectionsPage } from "@/features/connections/pages/ConnectionsPage";
import { ProfilePage } from "@/features/profiles/pages/ProfilePage";
import { PublicProfilePage } from "@/features/profiles/pages/PublicProfilePage";
import { SettingsPage } from "@/features/profiles/pages/SettingsPage";

export const routes: RouteObject[] = [
  {
    path: "/",
    element: <RootLayout />,
    errorElement: <RouteErrorView />,
    children: [
      { index: true, element: <HomePage /> },
      { path: "register", element: <RegisterPage /> },
      { path: "sign-in", element: <SignInPage /> },
      // Paths match the links the API emails (apps/api/app/modules/auth/service.py).
      { path: "verify-email", element: <VerifyEmailPage /> },
      { path: "forgot-password", element: <ForgotPasswordPage /> },
      { path: "reset-password", element: <ResetPasswordPage /> },
      {
        path: "profile",
        element: (
          <RequireVerified>
            <ProfilePage />
          </RequireVerified>
        ),
      },
      {
        path: "people/:userId",
        element: (
          <RequireVerified>
            <PublicProfilePage />
          </RequireVerified>
        ),
      },
      {
        path: "connections",
        element: (
          <RequireVerified>
            <ConnectionsPage />
          </RequireVerified>
        ),
      },
      {
        path: "settings",
        element: (
          <RequireAuth>
            <SettingsPage />
          </RequireAuth>
        ),
      },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
];
