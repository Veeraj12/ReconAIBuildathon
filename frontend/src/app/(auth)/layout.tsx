"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuthContext } from "@/features/auth/AuthProvider";

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  const { user, isLoading } = useAuthContext();
  const router = useRouter();

  // Restores what the deleted proxy.ts used to try to do: a user who
  // is already authenticated has no business on /login or /register.
  // The context provider has the real session state (from /auth/me),
  // unlike the middleware which could only guess from a cookie it was
  // never able to see. Deliberately does NOT block rendering while
  // isLoading — an anonymous visitor should see the form immediately.
  useEffect(() => {
    if (user && !isLoading) {
      router.replace("/reconcile");
    }
  }, [user, isLoading, router]);

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-slate-50 px-4 py-10">
      {/* Ambient radial glow */}
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(60rem_40rem_at_50%_-10%,rgba(13,148,136,0.16),transparent_70%)]" />
      {/* Subtle grid pattern, faded toward the edges */}
      <div className="pointer-events-none absolute inset-0 [background-image:linear-gradient(to_right,rgba(100,116,139,0.09)_1px,transparent_1px),linear-gradient(to_bottom,rgba(100,116,139,0.09)_1px,transparent_1px)] [background-size:44px_44px] [mask-image:radial-gradient(52rem_38rem_at_50%_40%,black,transparent)]" />
      <div className="relative w-full max-w-md">{children}</div>
    </div>
  );
}
