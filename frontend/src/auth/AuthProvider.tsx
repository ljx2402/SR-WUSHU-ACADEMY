import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

import { ApiError, isApiError } from "../api/errors";
import type { Me } from "../api/types";
import { useServices } from "../app/services";

/**
 * Sign-in state for the whole app, backed by the existing Django token API.
 *
 * - signIn: POST /api/auth/token/ → store token → load /api/me/ (roles, capabilities).
 * - /api/me/ is re-checked when the window regains focus, so role or capability
 *   changes made by an administrator are picked up.
 * - Any 401 (expired, revoked after a role/password change, deactivated,
 *   signed out elsewhere) clears the token and returns to the login page with
 *   a notice. 403s are handled by the pages (access denied), not here.
 */

export type AuthStatus = "checking" | "signedOut" | "signedIn" | "error";

export interface AuthContextValue {
  status: AuthStatus;
  me: Me | null;
  notice: string | null;
  error: ApiError | null;
  signIn(username: string, password: string): Promise<void>;
  signOut(): Promise<void>;
  retry(): void;
}

export const SESSION_EXPIRED_NOTICE = "Your session has expired or was ended. Please sign in again.";
export const SIGNED_OUT_NOTICE = "You have signed out.";

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const services = useServices();
  const queryClient = useQueryClient();
  const [token, setToken] = useState<string | null>(() => services.tokens.get());
  const [notice, setNotice] = useState<string | null>(null);

  // Registered during render so it is in place before the first request.
  services.setUnauthorizedHandler(() => {
    services.tokens.clear();
    queryClient.clear();
    setToken(null);
    setNotice(SESSION_EXPIRED_NOTICE);
  });

  const meQuery = useQuery({
    queryKey: ["me", token],
    queryFn: ({ signal }) => services.endpoints.me(signal),
    enabled: !!token,
    staleTime: 60_000,
    refetchOnWindowFocus: true,
    retry: false,
  });

  const signIn = useCallback(
    async (username: string, password: string) => {
      const { token: newToken } = await services.endpoints.signIn(username, password);
      services.tokens.set(newToken);
      queryClient.clear();
      setNotice(null);
      setToken(newToken);
    },
    [services, queryClient],
  );

  const signOut = useCallback(async () => {
    try {
      await services.endpoints.signOut(); // revokes the token on the server
    } catch {
      /* already invalid or offline: the local sign-in is cleared anyway */
    }
    services.tokens.clear();
    queryClient.clear();
    setToken(null);
    setNotice(SIGNED_OUT_NOTICE);
  }, [services, queryClient]);

  const value = useMemo<AuthContextValue>(() => {
    const error = meQuery.error;
    let status: AuthStatus;
    if (!token) status = "signedOut";
    else if (meQuery.data) status = "signedIn";
    else if (error) status = isApiError(error) && error.kind === "unauthorized" ? "signedOut" : "error";
    else status = "checking";
    return {
      status,
      me: token ? (meQuery.data ?? null) : null,
      notice,
      error: status === "error" ? (isApiError(error) ? error : new ApiError("network", null)) : null,
      signIn,
      signOut,
      retry: () => void meQuery.refetch(),
    };
  }, [token, meQuery, notice, signIn, signOut]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}

/** The signed-in user (only use inside routes guarded by RequireAuth). */
export function useMe(): Me {
  const { me } = useAuth();
  if (!me) throw new Error("useMe used outside an authenticated route");
  return me;
}
