"use client";

import { createContext, useContext, useEffect, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "./useAuth";

type AuthContextValue = ReturnType<typeof useAuth>;

const AuthContext = createContext<AuthContextValue | null>(null);

/**
 * Single instance of the auth state for the whole app, mounted in the
 * root layout. Previously every component (AuthCard, AppSidebar,
 * PatternsPanel…) called useAuth() directly — each got its own
 * independent /auth/me fetch and its own `user` state, so a user set by
 * a successful login in AuthCard was invisible to AppSidebar and vice
 * versa. One provider means one fetch, one state, everywhere.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const value = useAuth();
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

/** Must be rendered inside <AuthProvider> (root layout). */
export function useAuthContext(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuthContext must be used within an AuthProvider");
  }
  return ctx;
}

/**
 * Route guard for the protected (app) group. Distinguishes the three
 * states the proxy.ts middleware used to gate (now deleted — it gated
 * on a frontend-origin cookie that can never exist when the JWTs are
 * set on the cross-origin backend domain): while the initial /auth/me
 * check is pending we show a quiet loading state; only a *completed*
 * unauthenticated check redirects to /login.
 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { user, isLoading } = useAuthContext();
  const router = useRouter();

  useEffect(() => {
    if (!isLoading && !user) {
      router.replace("/login");
    }
  }, [isLoading, user, router]);

  if (isLoading || !user) {
    return (
      <div className="flex h-screen items-center justify-center bg-[#0E0E11] text-sm text-neutral-500">
        Checking your session…
      </div>
    );
  }

  return <>{children}</>;
}
