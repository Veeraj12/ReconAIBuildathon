"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { authApi } from "./api";
import type { AuthUser } from "./types";
import { ApiError } from "@/lib/api-client";

interface UseAuthResult {
  user: AuthUser | null;
  isLoading: boolean;
  error: string | null;
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

export function useAuth(): UseAuthResult {
  const router = useRouter();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    authApi
      .me()
      .then((res) => setUser(res.user))
      .catch(() => setUser(null))
      .finally(() => setIsLoading(false));
  }, []);

  const login = useCallback(
    async (username: string, password: string) => {
      setError(null);
      setIsLoading(true);
      try {
        const res = await authApi.login({ username, password });
        // The JWTs ride in httpOnly Set-Cookie headers on this response
        // (accepted by the browser for the backend's own domain), so
        // there is nothing else to persist — the user object is all the
        // client needs. The shared AuthProvider carries this user
        // across the client-side navigation to the (app) layout, whose
        // guard reads it instead of re-authenticating.
        setUser(res.user);
        router.push("/reconcile");
        return;
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Login failed.");
      } finally {
        setIsLoading(false);
      }
    },
    [router]
  );

  const register = useCallback(
    async (username: string, email: string, password: string) => {
      setError(null);
      setIsLoading(true);
      try {
        const res = await authApi.register({ username, email, password });
        setUser(res.user);
        router.push("/reconcile");
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Registration failed.");
      } finally {
        setIsLoading(false);
      }
    },
    [router]
  );

  const logout = useCallback(async () => {
    await authApi.logout();
    setUser(null);
    router.push("/login");
  }, [router]);

  return { user, isLoading, error, login, register, logout };
}
