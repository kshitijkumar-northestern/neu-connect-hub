import { useState } from "react";

import { Button } from "@/components/ui/button";
import { useAuth } from "@/features/auth/AuthProvider";

/** The API couldn't say whether the student is signed in (offline, or the API is down). */
export function AuthUnavailable() {
  const { refresh } = useAuth();
  const [retrying, setRetrying] = useState(false);

  return (
    <div role="alert" className="flex max-w-sm flex-col items-start gap-3">
      <p>Can't reach NU Connect Hub. Check your connection and try again.</p>
      <Button
        variant="outline"
        disabled={retrying}
        onClick={() => {
          setRetrying(true);
          void refresh().finally(() => setRetrying(false));
        }}
      >
        Try again
      </Button>
    </div>
  );
}
