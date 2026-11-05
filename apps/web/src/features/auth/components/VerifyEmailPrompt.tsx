import { AuthLayout } from "@/features/auth/components/AuthLayout";
import { ResendVerification } from "@/features/auth/components/ResendVerification";

/** Shown instead of a verified-only page until the student confirms their address. */
export function VerifyEmailPrompt({ email }: { email: string }) {
  return (
    <AuthLayout
      title="Verify your email"
      description={
        <>
          Open the link we sent to <strong className="text-foreground">{email}</strong> to finish setting up your
          account.
        </>
      }
    >
      <ResendVerification email={email} />
    </AuthLayout>
  );
}
