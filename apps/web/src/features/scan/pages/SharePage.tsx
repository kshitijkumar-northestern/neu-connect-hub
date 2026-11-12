import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router";
import { encode } from "uqr";

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
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { FormError } from "@/features/auth/components/FormError";
import { errorMessage } from "@/features/auth/errors";
import { ScanCodeDialog } from "@/features/scan/components/ScanCodeDialog";
import { QrCode } from "@/features/scan/components/QrCode";
import { scanApi, type ConnectionCode, type ConnectionCodeStatus } from "@/features/scan/api";

type LoadState = "loading" | "ready" | "error";

const EXPIRY_FORMAT: Intl.DateTimeFormatOptions = { month: "short", day: "numeric", year: "numeric" };

// What to tell a student whose code isn't active, by status. A Record so TypeScript catches a
// future status that this page doesn't yet explain.
const INACTIVE_EXPLANATION: Record<Exclude<ConnectionCodeStatus, "active">, string> = {
  off: "Turn on sharing to get a QR code and link other students can scan to connect with you.",
  expired: "Your code has expired. Create a new one to keep sharing your profile.",
};

function ActiveCode({
  code,
  busy,
  copied,
  rotateOpen,
  onRotateOpenChange,
  onCopy,
  onRotate,
  onDisable,
}: {
  code: Extract<ConnectionCode, { status: "active" }>;
  busy: boolean;
  copied: boolean;
  rotateOpen: boolean;
  onRotateOpenChange: (open: boolean) => void;
  onCopy: () => void;
  onRotate: () => void;
  onDisable: () => void;
}) {
  const matrix = useMemo(() => encode(code.url), [code.url]);
  const expires = new Date(code.expires_at).toLocaleDateString(undefined, EXPIRY_FORMAT);

  return (
    <div className="flex flex-col gap-6">
      <div className="h-48 w-48">
        <QrCode matrix={matrix} aria-label="QR code for your profile link" className="h-full w-full" />
      </div>
      <div className="flex max-w-md items-center gap-2">
        <Input readOnly aria-label="Your profile link" value={code.url} onFocus={(event) => event.target.select()} />
        <Button type="button" variant="outline" disabled={busy} onClick={onCopy}>
          {copied ? "Copied" : "Copy link"}
        </Button>
      </div>
      <p className="text-sm text-muted-foreground">Expires {expires}</p>
      <div className="flex gap-2">
        <AlertDialog open={rotateOpen} onOpenChange={onRotateOpenChange}>
          <AlertDialogTrigger asChild>
            <Button type="button" variant="outline" disabled={busy}>
              Get a new code
            </Button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Get a new code?</AlertDialogTitle>
              <AlertDialogDescription>Your old code stops working as soon as you do this.</AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
              {/* A plain button, not AlertDialogAction: that primitive always closes the dialog on
                  click, which would hide a rate-limit or other rotate error. */}
              <Button type="button" disabled={busy} onClick={onRotate}>
                Get a new code
              </Button>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
        <Button type="button" variant="outline" disabled={busy} onClick={onDisable}>
          Turn off sharing
        </Button>
      </div>
    </div>
  );
}

function InactiveCode({ status, busy, onCreate }: { status: "off" | "expired"; busy: boolean; onCreate: () => void }) {
  return (
    <div className="flex flex-col gap-4">
      <p className="text-muted-foreground">{INACTIVE_EXPLANATION[status]}</p>
      <Button type="button" disabled={busy} onClick={onCreate} className="self-start">
        Create a code
      </Button>
    </div>
  );
}

/** Your own scan-to-connect code: a QR and link while it's active, and the controls to rotate or
 * turn sharing off. Also the door to scanning someone else's code. */
export function SharePage() {
  const [load, setLoad] = useState<LoadState>("loading");
  const [code, setCode] = useState<ConnectionCode>();
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [rotateOpen, setRotateOpen] = useState(false);
  const [scanOpen, setScanOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    scanApi
      .myCode()
      .then((result) => {
        if (cancelled) return;
        setCode(result);
        setLoad("ready");
      })
      .catch(() => {
        if (!cancelled) setLoad("error");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!copied) return;
    const timeout = window.setTimeout(() => setCopied(false), 2000);
    return () => window.clearTimeout(timeout);
  }, [copied]);

  async function copyLink(url: string) {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
    } catch (failure) {
      setError(errorMessage(failure));
    }
  }

  async function rotate() {
    setBusy(true);
    setError(undefined);
    try {
      const result = await scanApi.rotate();
      setCode(result);
      setRotateOpen(false);
      setCopied(false);
    } catch (failure) {
      setRotateOpen(false);
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }

  async function disable() {
    setBusy(true);
    setError(undefined);
    try {
      await scanApi.disable();
      setCode((current) => (current ? { ...current, status: "off", url: null, expires_at: null } : current));
      setCopied(false);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }

  if (load === "loading") {
    return (
      <p role="status" className="text-muted-foreground">
        Loading…
      </p>
    );
  }

  if (load === "error" || !code) {
    return <p role="alert">Can't load your sharing code right now. Check your connection and try again.</p>;
  }

  return (
    <section className="flex flex-col gap-10">
      <h1 className="text-2xl font-semibold tracking-tight">Share your profile</h1>
      <FormError>{error}</FormError>

      {code.shareable === false ? (
        <Alert>
          <AlertDescription>
            Your profile is hidden from discovery, so only students you're already connected with will see it when
            they scan your code. <Link to="/profile">Turn on discovery</Link>
          </AlertDescription>
        </Alert>
      ) : null}

      {code.status === "active" ? (
        <ActiveCode
          code={code}
          busy={busy}
          copied={copied}
          rotateOpen={rotateOpen}
          onRotateOpenChange={setRotateOpen}
          onCopy={() => void copyLink(code.url)}
          onRotate={() => void rotate()}
          onDisable={() => void disable()}
        />
      ) : (
        <InactiveCode status={code.status} busy={busy} onCreate={() => void rotate()} />
      )}

      <div>
        <Button type="button" variant="outline" onClick={() => setScanOpen(true)}>
          Scan a code
        </Button>
      </div>
      <ScanCodeDialog open={scanOpen} onOpenChange={setScanOpen} />
    </section>
  );
}
