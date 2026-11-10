import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { RouteObject } from "react-router";
import { describe, expect, it } from "vitest";

import { routes } from "@/app/routes";
import { RequireAuth, RequireVerified } from "@/features/auth/guards";
import { CSRF_COOKIE, CSRF_HEADER } from "@/lib/http";
import { ADA, apiError, fakeApi, type Reply } from "@/test/fake-api";
import { renderApp } from "@/test/render-app";

const PASSWORD = "correct horse battery";

/** The real route table plus two guarded pages, nested under the same root (shell + errors). */
function withGuardedPages(): RouteObject[] {
  const [root] = routes;
  if (!root) throw new Error("routes must define a root route");
  return [
    {
      path: "/",
      element: root.element,
      errorElement: root.errorElement,
      children: [
        { path: "private", element: <RequireAuth><p>private page</p></RequireAuth> },
        {
          path: "verified-only",
          element: (
            <RequireVerified>
              <p>verified page</p>
            </RequireVerified>
          ),
        },
        ...(root.children ?? []),
      ],
    },
  ];
}

describe("auth state", () => {
  it("shows the signed-in account in the header after loading /me", async () => {
    fakeApi({ "GET /me": [200, ADA] });
    renderApp("/");

    const header = screen.getByRole("banner");
    expect(await within(header).findByRole("button", { name: /Account: ada@northeastern\.edu/ })).toBeInTheDocument();
    expect(within(header).queryByRole("link", { name: "Sign in" })).not.toBeInTheDocument();
  });

  it("offers sign in when there is no session", async () => {
    fakeApi({ "GET /me": apiError(401, "unauthorized") });
    renderApp("/");

    expect(await within(screen.getByRole("banner")).findByRole("link", { name: "Sign in" })).toHaveAttribute(
      "href",
      "/sign-in",
    );
  });

  it("signs out with the CSRF header and shows sign in again, even if the request fails", async () => {
    const user = userEvent.setup();
    document.cookie = `${CSRF_COOKIE}=csrf-abc; path=/`;
    const api = fakeApi({ "GET /me": [200, ADA], "POST /auth/logout": apiError(500, "internal_error") });
    renderApp("/");

    await user.click(await screen.findByRole("button", { name: /Account:/ }));
    await user.click(await screen.findByRole("menuitem", { name: "Sign out" }));

    expect(await within(screen.getByRole("banner")).findByRole("link", { name: "Sign in" })).toBeInTheDocument();
    expect(api.callsTo("POST /auth/logout")[0]?.headers.get(CSRF_HEADER)).toBe("csrf-abc");
    document.cookie = `${CSRF_COOKIE}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/`;
  });
});

describe("auth state under slow or failing requests", () => {
  it("ignores a slow first /me that answers after the student has signed in", async () => {
    const user = userEvent.setup();
    let answerMe: (reply: Reply) => void = () => undefined;
    const slowMe = new Promise<Reply>((resolve) => {
      answerMe = resolve;
    });
    fakeApi({ "GET /me": () => slowMe, "POST /auth/login": [200, ADA] });
    renderApp("/sign-in");

    await user.type(await screen.findByLabelText("University email"), "ada@northeastern.edu");
    await user.type(screen.getByLabelText("Password"), PASSWORD);
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    const header = screen.getByRole("banner");
    expect(await within(header).findByRole("button", { name: /Account:/ })).toBeInTheDocument();

    answerMe(apiError(401, "unauthorized"));
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(within(header).getByRole("button", { name: /Account:/ })).toBeInTheDocument();
    expect(within(header).queryByRole("link", { name: "Sign in" })).not.toBeInTheDocument();
  });

  it("treats an unreachable API as unavailable, not signed out, and recovers on retry", async () => {
    const user = userEvent.setup();
    const api = fakeApi({ "GET /me": apiError(503, "not_ready") });
    const { router } = renderApp("/private", withGuardedPages());

    expect(await screen.findByText(/Can't reach NU Connect Hub/)).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/private");

    api.setRoute("GET /me", [200, ADA]);
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByText("private page")).toBeInTheDocument();
  });

  it("signing out on a protected page goes straight home, never via sign in", async () => {
    const user = userEvent.setup();
    fakeApi({ "GET /me": [200, ADA], "POST /auth/logout": [204] });
    const { router } = renderApp("/private", withGuardedPages());
    const visited: string[] = [];
    router.subscribe((state) => visited.push(state.location.pathname));

    await user.click(await screen.findByRole("button", { name: /Account:/ }));
    await user.click(await screen.findByRole("menuitem", { name: "Sign out" }));

    await waitFor(() => expect(router.state.location.pathname).toBe("/"));
    expect(visited).not.toContain("/sign-in");
  });
});

describe("registration", () => {
  it("creates the account and asks the student to check their inbox", async () => {
    const user = userEvent.setup();
    const api = fakeApi({
      "GET /me": apiError(401, "unauthorized"),
      "POST /auth/register": [202, { status: "verification_sent" }],
    });
    renderApp("/register");

    await user.type(await screen.findByLabelText("University email"), "ada@northeastern.edu");
    await user.type(screen.getByLabelText("Password"), PASSWORD);
    await user.click(screen.getByRole("button", { name: "Create account" }));

    expect(await screen.findByRole("heading", { name: "Check your inbox" })).toBeInTheDocument();
    expect(screen.getByText(/ada@northeastern\.edu/)).toBeInTheDocument();
    expect(api.callsTo("POST /auth/register")[0]?.body).toEqual({
      email: "ada@northeastern.edu",
      password: PASSWORD,
    });
  });

  it("checks password length before sending anything", async () => {
    const user = userEvent.setup();
    const api = fakeApi({ "GET /me": apiError(401, "unauthorized") });
    renderApp("/register");

    await user.type(await screen.findByLabelText("University email"), "ada@northeastern.edu");
    await user.type(screen.getByLabelText("Password"), "short");
    await user.click(screen.getByRole("button", { name: "Create account" }));

    expect(await screen.findByText("Use at least 12 characters.")).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toHaveAttribute("aria-invalid", "true");
    expect(api.callsTo("POST /auth/register")).toHaveLength(0);
  });

  it("shows a non-campus domain error on the email field", async () => {
    const user = userEvent.setup();
    fakeApi({
      "GET /me": apiError(401, "unauthorized"),
      "POST /auth/register": apiError(422, "email_domain_not_allowed"),
    });
    renderApp("/register");

    await user.type(await screen.findByLabelText("University email"), "ada@gmail.com");
    await user.type(screen.getByLabelText("Password"), PASSWORD);
    await user.click(screen.getByRole("button", { name: "Create account" }));

    expect(await screen.findByText("Use your university email address.")).toBeInTheDocument();
    expect(screen.getByLabelText("University email")).toHaveAttribute("aria-invalid", "true");
  });

  it("sends signed-in users home", async () => {
    fakeApi({ "GET /me": [200, ADA] });
    const { router } = renderApp("/register");

    await waitFor(() => expect(router.state.location.pathname).toBe("/"));
  });
});

describe("sign in", () => {
  it("signs in and returns to the page the student came from", async () => {
    const user = userEvent.setup();
    const api = fakeApi({ "GET /me": apiError(401, "unauthorized"), "POST /auth/login": [200, ADA] });
    const { router } = renderApp("/private", withGuardedPages());

    await user.type(await screen.findByLabelText("University email"), "ada@northeastern.edu");
    await user.type(screen.getByLabelText("Password"), PASSWORD);
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByText("private page")).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/private");
    expect(api.callsTo("POST /auth/login")[0]?.body).toEqual({ email: "ada@northeastern.edu", password: PASSWORD });
  });

  it("explains a wrong email or password without revealing which", async () => {
    const user = userEvent.setup();
    fakeApi({ "GET /me": apiError(401, "unauthorized"), "POST /auth/login": apiError(401, "invalid_credentials") });
    renderApp("/sign-in");

    await user.type(await screen.findByLabelText("University email"), "ada@northeastern.edu");
    await user.type(screen.getByLabelText("Password"), "wrong password!!");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("That email and password don't match.");
  });

  it("puts a server validation error on the email field", async () => {
    const user = userEvent.setup();
    fakeApi({
      "GET /me": apiError(401, "unauthorized"),
      "POST /auth/login": [
        422,
        {
          error: {
            code: "validation_error",
            message: "The request is invalid.",
            request_id: "req-test",
            fields: [{ location: "body.email", message: "value is not a valid email address" }],
          },
        },
      ],
    });
    renderApp("/sign-in");

    await user.type(await screen.findByLabelText("University email"), "ada");
    await user.type(screen.getByLabelText("Password"), PASSWORD);
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByText("value is not a valid email address")).toBeInTheDocument();
    expect(screen.getByLabelText("University email")).toHaveAttribute("aria-invalid", "true");
  });

  it("tells a throttled student to wait", async () => {
    const user = userEvent.setup();
    fakeApi({ "GET /me": apiError(401, "unauthorized"), "POST /auth/login": apiError(429, "rate_limited") });
    renderApp("/sign-in");

    await user.type(await screen.findByLabelText("University email"), "ada@northeastern.edu");
    await user.type(screen.getByLabelText("Password"), PASSWORD);
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/Too many attempts/);
  });
});

describe("email verification link", () => {
  it("sends the token from the link once, removes it from the address bar and confirms", async () => {
    const api = fakeApi({
      "GET /me": apiError(401, "unauthorized"),
      "POST /auth/verify": [200, { status: "verified" }],
    });
    const { router } = renderApp("/verify-email#token=abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG");

    expect(await screen.findByRole("heading", { name: "Email verified" })).toBeInTheDocument();
    expect(api.callsTo("POST /auth/verify")).toHaveLength(1);
    expect(api.callsTo("POST /auth/verify")[0]?.body).toEqual({
      token: "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG",
    });
    expect(router.state.location.hash).toBe("");
  });

  it("offers a new link when the token is invalid or expired", async () => {
    const user = userEvent.setup();
    const api = fakeApi({
      "GET /me": apiError(401, "unauthorized"),
      "POST /auth/verify": apiError(400, "invalid_token"),
      "POST /auth/resend-verification": [202, { status: "verification_sent" }],
    });
    renderApp("/verify-email#token=expiredtokenexpiredtokenexpiredtoken12345");

    expect(await screen.findByText("This link is invalid or has expired.")).toBeInTheDocument();
    await user.type(screen.getByLabelText("University email"), "ada@northeastern.edu");
    await user.click(screen.getByRole("button", { name: "Send a new link" }));

    expect(await screen.findByText(/If that address still needs verifying/)).toBeInTheDocument();
    expect(api.callsTo("POST /auth/resend-verification")[0]?.body).toEqual({ email: "ada@northeastern.edu" });
  });
});

describe("password reset", () => {
  it("gives the same answer for any address", async () => {
    const user = userEvent.setup();
    fakeApi({
      "GET /me": apiError(401, "unauthorized"),
      "POST /auth/password-reset/request": [202, { status: "reset_requested" }],
    });
    renderApp("/forgot-password");

    await user.type(await screen.findByLabelText("University email"), "nobody@northeastern.edu");
    await user.click(screen.getByRole("button", { name: "Send reset link" }));

    expect(await screen.findByText(/If an account exists for nobody@northeastern\.edu/)).toBeInTheDocument();
  });

  it("sets a new password from the link and sends the student to sign in", async () => {
    const user = userEvent.setup();
    const token = "resettokenresettokenresettokenresettoken123";
    const api = fakeApi({
      "GET /me": apiError(401, "unauthorized"),
      "POST /auth/password-reset/confirm": [200, { status: "password_reset" }],
    });
    const { router } = renderApp(`/reset-password#token=${token}`);

    await user.type(await screen.findByLabelText("New password"), "a brand new passphrase");
    await user.type(screen.getByLabelText("Confirm new password"), "a different passphrase");
    await user.click(screen.getByRole("button", { name: "Set new password" }));
    expect(await screen.findByText("The passwords don't match.")).toBeInTheDocument();
    expect(api.callsTo("POST /auth/password-reset/confirm")).toHaveLength(0);

    await user.clear(screen.getByLabelText("Confirm new password"));
    await user.type(screen.getByLabelText("Confirm new password"), "a brand new passphrase");
    await user.click(screen.getByRole("button", { name: "Set new password" }));

    expect(await screen.findByRole("status")).toHaveTextContent(/Your password has been changed/);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/sign-in");
    expect(api.callsTo("POST /auth/password-reset/confirm")[0]?.body).toEqual({
      token,
      password: "a brand new passphrase",
    });
  });
});

describe("route guards", () => {
  it("sends signed-out visitors to sign in", async () => {
    fakeApi({ "GET /me": apiError(401, "unauthorized") });
    const { router } = renderApp("/private", withGuardedPages());

    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/sign-in");
    expect(screen.queryByText("private page")).not.toBeInTheDocument();
  });

  it("asks unverified students to verify before verified-only pages", async () => {
    fakeApi({ "GET /me": [200, { ...ADA, verified: false }] });
    renderApp("/verified-only", withGuardedPages());

    expect(await screen.findByRole("heading", { name: "Verify your email" })).toBeInTheDocument();
    expect(screen.queryByText("verified page")).not.toBeInTheDocument();
  });

  it("RequireVerified on its own still sends signed-out visitors to sign in", async () => {
    fakeApi({ "GET /me": apiError(401, "unauthorized") });
    const { router } = renderApp("/verified-only", withGuardedPages());

    await waitFor(() => expect(router.state.location.pathname).toBe("/sign-in"));
    expect(router.state.location.state).toEqual({ from: "/verified-only" });
  });

  it("shows verified-only pages to verified students", async () => {
    fakeApi({ "GET /me": [200, ADA] });
    renderApp("/verified-only", withGuardedPages());

    expect(await screen.findByText("verified page")).toBeInTheDocument();
  });
});
