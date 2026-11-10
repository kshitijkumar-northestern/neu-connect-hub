import { useEffect, useState } from "react";
import { useNavigate } from "react-router";

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { authApi } from "@/features/auth/api";
import { useAuth } from "@/features/auth/AuthProvider";
import { FormError } from "@/features/auth/components/FormError";
import { TextField } from "@/features/auth/components/TextField";
import { errorMessage } from "@/features/auth/errors";
import { trustApi, type BlockedUser } from "@/features/profiles/api";

/** Set on the navigation state HomePage reads after a successful account deletion. */
export interface HomeNotice {
  notice?: "account-deleted";
}

type LoadState = "loading" | "ready" | "error";

function BlockedStudentsSection() {
  const [load, setLoad] = useState<LoadState>("loading");
  const [blocks, setBlocks] = useState<BlockedUser[]>([]);
  const [error, setError] = useState<string>();

  useEffect(() => {
    let cancelled = false;
    trustApi
      .listBlocks()
      .then((result) => {
        if (cancelled) return;
        setBlocks(result);
        setLoad("ready");
      })
      .catch(() => {
        if (!cancelled) setLoad("error");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function unblock(userId: string) {
    setError(undefined);
    try {
      await trustApi.unblock(userId);
      setBlocks((current) => current.filter((block) => block.user_id !== userId));
    } catch (failure) {
      setError(errorMessage(failure));
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-lg font-medium">Blocked students</h2>
      <FormError>{error}</FormError>
      {load === "loading" ? (
        <p role="status" className="text-muted-foreground">
          Loading…
        </p>
      ) : null}
      {load === "error" ? <p role="alert">Can't load your blocked students right now.</p> : null}
      {load === "ready" && !blocks.length ? (
        <p className="text-muted-foreground">You haven't blocked anyone.</p>
      ) : null}
      {load === "ready" && blocks.length ? (
        <ul className="flex flex-col gap-2">
          {blocks.map((block) => (
            <li key={block.user_id} className="flex items-center justify-between gap-4 rounded-2xl border px-4 py-2">
              <span>{block.display_name ?? "A student without a profile"}</span>
              <Button variant="outline" size="sm" onClick={() => void unblock(block.user_id)}>
                Unblock
              </Button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function DeleteAccountSection() {
  const navigate = useNavigate();
  const { refresh } = useAuth();
  const [password, setPassword] = useState("");
  const [open, setOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string>();

  async function confirmDelete() {
    setDeleting(true);
    setError(undefined);
    try {
      await authApi.deleteAccount(password);
      setOpen(false);
      const notice: HomeNotice = { notice: "account-deleted" };
      // Home first, rendered synchronously (React Router otherwise renders navigations as a
      // transition): if the auth state flipped while this protected page was still on screen,
      // its guard would bounce the student to /sign-in.
      void Promise.resolve(navigate("/", { replace: true, state: notice, flushSync: true })).then(
        () => refresh(),
      );
    } catch (failure) {
      // Close the confirmation so the error and the password field are in view again.
      setOpen(false);
      setError(errorMessage(failure));
      setDeleting(false);
    }
  }

  return (
    <div className="flex max-w-sm flex-col gap-4">
      <h2 className="text-lg font-medium">Delete account</h2>
      <p className="text-sm text-muted-foreground">
        This permanently deletes your account and profile. This can't be undone.
      </p>
      <FormError>{error}</FormError>
      <TextField
        label="Password"
        type="password"
        autoComplete="current-password"
        required
        value={password}
        onChange={(event) => setPassword(event.target.value)}
      />
      <AlertDialog open={open} onOpenChange={setOpen}>
        <AlertDialogTrigger asChild>
          <Button variant="destructive" disabled={!password} className="self-start">
            Delete account
          </Button>
        </AlertDialogTrigger>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete your account?</AlertDialogTitle>
            <AlertDialogDescription>
              This is permanent. Your account and profile will be removed, and this can't be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>Cancel</AlertDialogCancel>
            {/* A plain button, not AlertDialogAction: that primitive always closes the dialog on
                click, which would hide a wrong-password error. */}
            <Button variant="destructive" disabled={deleting} onClick={() => void confirmDelete()}>
              Delete account
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

export function SettingsPage() {
  const { state } = useAuth();
  const verified = state.status === "signed-in" && state.user.verified;

  return (
    <section className="flex flex-col gap-10">
      <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
      {verified ? <BlockedStudentsSection /> : null}
      <DeleteAccountSection />
    </section>
  );
}
