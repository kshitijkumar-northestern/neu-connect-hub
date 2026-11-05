import { Logout03Icon, UserCircleIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { Link, useNavigate } from "react-router";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useAuth } from "@/features/auth/AuthProvider";

/** Header control: "Sign in" when signed out, the account menu when signed in. */
export function AccountMenu() {
  const { state, signOut } = useAuth();
  const navigate = useNavigate();

  // Unknown yet (or the API is unreachable): avoids flashing "Sign in" for signed-in students.
  if (state.status === "loading" || state.status === "unavailable") return null;
  if (state.status === "signed-out") {
    return (
      <Button asChild variant="ghost" size="sm">
        <Link to="/sign-in">Sign in</Link>
      </Button>
    );
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="sm" aria-label={`Account: ${state.user.email}`}>
          <HugeiconsIcon icon={UserCircleIcon} aria-hidden="true" />
          <span className="hidden max-w-48 truncate sm:inline">{state.user.email}</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuLabel className="max-w-64 truncate">{state.user.email}</DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          onSelect={() => {
            // Home first: signing out while on a protected page would otherwise bounce via /sign-in.
            void Promise.resolve(navigate("/", { replace: true })).then(signOut);
          }}
        >
          <HugeiconsIcon icon={Logout03Icon} aria-hidden="true" />
          Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
