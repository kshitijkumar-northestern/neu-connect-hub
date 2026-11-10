import { Logout03Icon, Settings02Icon, UserIcon, UserCircleIcon, UserMultipleIcon } from "@hugeicons/core-free-icons";
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
        <DropdownMenuItem asChild>
          <Link to="/profile">
            <HugeiconsIcon icon={UserIcon} aria-hidden="true" />
            Your profile
          </Link>
        </DropdownMenuItem>
        <DropdownMenuItem asChild>
          <Link to="/connections">
            <HugeiconsIcon icon={UserMultipleIcon} aria-hidden="true" />
            Connections
          </Link>
        </DropdownMenuItem>
        <DropdownMenuItem asChild>
          <Link to="/settings">
            <HugeiconsIcon icon={Settings02Icon} aria-hidden="true" />
            Settings
          </Link>
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          onSelect={() => {
            // Home first, rendered synchronously (navigations otherwise render as a transition):
            // signing out while a protected page is still on screen would bounce via /sign-in.
            void Promise.resolve(navigate("/", { replace: true, flushSync: true })).then(signOut);
          }}
        >
          <HugeiconsIcon icon={Logout03Icon} aria-hidden="true" />
          Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
