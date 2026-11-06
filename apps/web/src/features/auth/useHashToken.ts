import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router";

const TOKEN_IN_HASH = /(?:^#|&)token=([A-Za-z0-9_-]+)/;

/**
 * The single-use token from an emailed link (/verify-email#token=...). Links carry it in the
 * fragment so it never reaches a server or access log; this reads it once, then removes it from
 * the address bar and history so it isn't left on screen, in bookmarks or in screenshots.
 */
export function useHashToken(): string | null {
  const location = useLocation();
  const navigate = useNavigate();
  const [token] = useState(() => TOKEN_IN_HASH.exec(location.hash)?.[1] ?? null);

  useEffect(() => {
    if (location.hash) void navigate({ pathname: location.pathname, search: location.search }, { replace: true });
  }, [location.hash, location.pathname, location.search, navigate]);

  return token;
}
