"use client";

import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  AlertCircle,
  ArrowRight,
  Building2,
  Eye,
  EyeOff,
  Loader2,
  Lock,
  Mail,
  ShieldCheck,
  User,
} from "lucide-react";
import { useAuthContext } from "@/features/auth/AuthProvider";

type Mode = "signin" | "register";

interface AuthCardProps {
  initialMode?: Mode;
}

const STRENGTH_LEVELS = [
  { label: "Too weak", bar: "bg-rose-500", text: "text-rose-600" },
  { label: "Weak", bar: "bg-orange-500", text: "text-orange-600" },
  { label: "Fair", bar: "bg-amber-500", text: "text-amber-600" },
  { label: "Good", bar: "bg-emerald-500", text: "text-emerald-600" },
  { label: "Strong", bar: "bg-emerald-600", text: "text-emerald-600" },
];

function scorePassword(password: string) {
  let score = 0;
  if (password.length >= 8) score += 1;
  if (/[a-z]/.test(password) && /[A-Z]/.test(password)) score += 1;
  if (/\d/.test(password)) score += 1;
  if (/[^A-Za-z0-9]/.test(password)) score += 1;
  return score;
}

const inputClass =
  "peer w-full rounded-xl border border-slate-200 bg-slate-50/70 py-2.5 pl-10 pr-3 text-sm text-slate-900 placeholder:text-slate-400 outline-none transition-all duration-200 focus:border-emerald-500 focus:bg-white focus:ring-2 focus:ring-emerald-500/20";

const iconClass =
  "pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400 transition-colors peer-focus:text-emerald-500";

export function AuthCard({ initialMode = "signin" }: AuthCardProps) {
  const { login, register, error } = useAuthContext();
  const [mode, setMode] = useState<Mode>(initialMode);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [remember, setRemember] = useState(true);
  const [resetHint, setResetHint] = useState(false);

  const [fullName, setFullName] = useState("");
  const [company, setCompany] = useState("");
  const [workEmail, setWorkEmail] = useState("");
  const [acceptedTerms, setAcceptedTerms] = useState(false);

  const strength = password ? scorePassword(password) : 0;
  const strengthMeta = STRENGTH_LEVELS[strength];
  const displayError = localError ?? error;

  function switchMode(next: Mode) {
    setMode(next);
    setLocalError(null);
    setResetHint(false);
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setLocalError(null);

    if (mode === "signin") {
      if (!identifier.trim() || !password) {
        setLocalError("Enter your work email and password to continue.");
        return;
      }
      setIsSubmitting(true);
      await login(identifier.trim(), password);
      setIsSubmitting(false);
      return;
    }

    if (!fullName.trim() || !workEmail.trim() || !password) {
      setLocalError("Please complete all required fields.");
      return;
    }
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(workEmail.trim())) {
      setLocalError("Enter a valid work email address.");
      return;
    }
    if (password.length < 8) {
      setLocalError("Use a password with at least 8 characters.");
      return;
    }
    if (!acceptedTerms) {
      setLocalError("Please accept the Terms of Service to create an account.");
      return;
    }

    setIsSubmitting(true);
    // The username IS the work email — not the full name. The backend
    // login field is "Work Email or Username", and Django's built-in
    // username rules (uniqueness + the UnicodeUsernameValidator charset)
    // are written for email-shaped identifiers; registering with a
    // space-containing full name instead created accounts like "Jordan
    // Miller" that collide across users (uniqueness violation → 500)
    // and could never be entered as an email. The full name is not
    // persisted — UserProfile has no display-name field today.
    await register(workEmail.trim(), workEmail.trim(), password);
    setIsSubmitting(false);
  }

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
      className="w-full overflow-hidden rounded-2xl border border-slate-200 bg-white/90 shadow-[0_20px_60px_-24px_rgba(15,23,42,0.28)] backdrop-blur-xl"
    >
      {/* Brand header */}
      <div className="flex flex-col items-center px-8 pt-8 text-center">
        <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-gradient-to-br from-emerald-600 to-teal-600 text-lg font-bold text-white shadow-lg shadow-emerald-600/20">
          R
        </div>
        <h1 className="mt-4 text-xl font-semibold tracking-tight text-slate-900">
          {mode === "signin" ? "Welcome back" : "Create your workspace"}
        </h1>
        <p className="mt-1 text-sm text-slate-500">
          ReconAI · Bank &amp; Tally Reconciliation
        </p>
      </div>

      <div className="px-8 pb-8 pt-6">
        {/* Mode tabs */}
        <div className="relative mb-6 flex rounded-xl bg-slate-100 p-1">
          {(["signin", "register"] as Mode[]).map((tab) => {
            const active = mode === tab;
            return (
              <button
                key={tab}
                type="button"
                onClick={() => switchMode(tab)}
                className="relative z-10 flex-1 rounded-lg px-3 py-2 text-sm font-semibold transition-colors duration-200"
              >
                {active && (
                  <motion.span
                    layoutId="auth-tab-indicator"
                    className="absolute inset-0 -z-10 rounded-lg bg-white shadow-sm"
                    transition={{ type: "spring", stiffness: 420, damping: 34 }}
                  />
                )}
                <span className={active ? "text-slate-900" : "text-slate-500 hover:text-slate-700"}>
                  {tab === "signin" ? "Sign In" : "Create Account"}
                </span>
              </button>
            );
          })}
        </div>

        <AnimatePresence mode="wait" initial={false}>
          <motion.form
            key={mode}
            onSubmit={handleSubmit}
            initial={{ opacity: 0, x: mode === "signin" ? -14 : 14 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: mode === "signin" ? 14 : -14 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
            className="space-y-4"
            noValidate
          >
            {mode === "signin" ? (
              <>
                <div className="space-y-1.5">
                  <label htmlFor="identifier" className="block text-[13px] font-medium text-slate-700">
                    Work Email or Username
                  </label>
                  <div className="relative">
                    <input
                      id="identifier"
                      type="text"
                      value={identifier}
                      onChange={(e) => setIdentifier(e.target.value)}
                      placeholder="you@company.com"
                      autoComplete="username"
                      autoFocus
                      className={inputClass}
                    />
                    <User className={iconClass} />
                  </div>
                </div>

                <div className="space-y-1.5">
                  <label htmlFor="password" className="block text-[13px] font-medium text-slate-700">
                    Password
                  </label>
                  <div className="relative">
                    <input
                      id="password"
                      type={showPassword ? "text" : "password"}
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      placeholder="••••••••"
                      autoComplete="current-password"
                      className={`${inputClass} pr-11`}
                    />
                    <Lock className={iconClass} />
                    <button
                      type="button"
                      onClick={() => setShowPassword((v) => !v)}
                      aria-label={showPassword ? "Hide password" : "Show password"}
                      className="absolute right-3 top-1/2 -translate-y-1/2 rounded-md p-0.5 text-slate-400 transition-colors hover:text-slate-600"
                    >
                      {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                    </button>
                  </div>
                </div>
                 <div className="flex flex-col items-end gap-1">
                  <label className="flex cursor-pointer select-none items-center gap-2 text-[13px] text-slate-600">
                    {`Use Username - "TestAdmin" & Password - "Admin@123" for demo`}
                  </label>
                  </div>

                <div className="flex items-center justify-between">
                  <label className="flex cursor-pointer select-none items-center gap-2 text-[13px] text-slate-600">
                    <input
                      type="checkbox"
                      checked={remember}
                      onChange={(e) => setRemember(e.target.checked)}
                      className="h-4 w-4 rounded border-slate-300 accent-emerald-600 focus:ring-2 focus:ring-emerald-500/30"
                    />
                    Remember me
                  </label>
                  <button
                    type="button"
                    onClick={() => setResetHint((v) => !v)}
                    className="text-[13px] font-medium text-emerald-600 transition-colors hover:text-emerald-700"
                  >
                    Forgot password?
                  </button>
                </div>

                {resetHint && (
                  <p className="animate-fade-in text-xs text-slate-400">
                    Contact your workspace administrator to reset your password.
                  </p>
                )}
              </>
            ) : (
              <>
                <div className="space-y-1.5">
                  <label htmlFor="fullName" className="block text-[13px] font-medium text-slate-700">
                    Full Name
                  </label>
                  <div className="relative">
                    <input
                      id="fullName"
                      type="text"
                      value={fullName}
                      onChange={(e) => setFullName(e.target.value)}
                      placeholder="Jordan Miller"
                      autoComplete="name"
                      autoFocus
                      className={inputClass}
                    />
                    <User className={iconClass} />
                  </div>
                </div>

                <div className="space-y-1.5">
                  <label htmlFor="company" className="block text-[13px] font-medium text-slate-700">
                    Company / Organization{" "}
                    <span className="font-normal text-slate-400">(optional)</span>
                  </label>
                  <div className="relative">
                    <input
                      id="company"
                      type="text"
                      value={company}
                      onChange={(e) => setCompany(e.target.value)}
                      placeholder="Acme Finance Ltd."
                      autoComplete="organization"
                      className={inputClass}
                    />
                    <Building2 className={iconClass} />
                  </div>
                </div>

                <div className="space-y-1.5">
                  <label htmlFor="workEmail" className="block text-[13px] font-medium text-slate-700">
                    Work Email
                  </label>
                  <div className="relative">
                    <input
                      id="workEmail"
                      type="email"
                      value={workEmail}
                      onChange={(e) => setWorkEmail(e.target.value)}
                      placeholder="you@company.com"
                      autoComplete="email"
                      className={inputClass}
                    />
                    <Mail className={iconClass} />
                  </div>
                </div>

                <div className="space-y-1.5">
                  <label htmlFor="newPassword" className="block text-[13px] font-medium text-slate-700">
                    Password
                  </label>
                  <div className="relative">
                    <input
                      id="newPassword"
                      type={showPassword ? "text" : "password"}
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      placeholder="At least 8 characters"
                      autoComplete="new-password"
                      className={`${inputClass} pr-11`}
                    />
                    <Lock className={iconClass} />
                    <button
                      type="button"
                      onClick={() => setShowPassword((v) => !v)}
                      aria-label={showPassword ? "Hide password" : "Show password"}
                      className="absolute right-3 top-1/2 -translate-y-1/2 rounded-md p-0.5 text-slate-400 transition-colors hover:text-slate-600"
                    >
                      {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                    </button>
                  </div>

                  {password && (
                    <div className="animate-fade-in pt-1">
                      <div className="flex gap-1.5">
                        {[0, 1, 2, 3].map((i) => (
                          <span
                            key={i}
                            className={`h-1 flex-1 rounded-full transition-all duration-300 ${
                              i < Math.max(strength, 1) ? strengthMeta.bar : "bg-slate-200"
                            }`}
                          />
                        ))}
                      </div>
                      <p className={`mt-1.5 text-xs font-medium ${strengthMeta.text}`}>
                        {strengthMeta.label}
                      </p>
                    </div>
                  )}
                </div>

                <label className="flex cursor-pointer select-none items-start gap-2.5 pt-1 text-[13px] text-slate-600">
                  <input
                    type="checkbox"
                    checked={acceptedTerms}
                    onChange={(e) => setAcceptedTerms(e.target.checked)}
                    className="mt-0.5 h-4 w-4 rounded border-slate-300 accent-emerald-600 focus:ring-2 focus:ring-emerald-500/30"
                  />
                  <span>
                    I agree to the{" "}
                    <a href="#" className="font-medium text-emerald-600 hover:text-emerald-700">
                      Terms of Service
                    </a>{" "}
                    and{" "}
                    <a href="#" className="font-medium text-emerald-600 hover:text-emerald-700">
                      Privacy Policy
                    </a>
                    .
                  </span>
                </label>
              </>
            )}

            {displayError && (
              <div className="animate-fade-in flex items-start gap-2 rounded-lg border border-rose-100 bg-rose-50 px-3 py-2.5 text-[13px] font-medium text-rose-600">
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                <span>{displayError}</span>
              </div>
            )}

            <motion.button
              type="submit"
              disabled={isSubmitting}
              whileHover={isSubmitting ? undefined : { scale: 1.01 }}
              whileTap={isSubmitting ? undefined : { scale: 0.985 }}
              transition={{ type: "spring", stiffness: 400, damping: 26 }}
              className="flex w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-emerald-600 to-teal-600 px-4 py-2.5 text-sm font-semibold text-white shadow-lg shadow-emerald-600/20 transition-colors hover:from-emerald-700 hover:to-teal-700 disabled:cursor-not-allowed disabled:opacity-70"
            >
              {isSubmitting ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  {mode === "signin" ? "Signing in..." : "Creating account..."}
                </>
              ) : (
                <>
                  {mode === "signin" ? "Sign In" : "Create Account"}
                  <ArrowRight className="h-4 w-4" />
                </>
              )}
            </motion.button>
          </motion.form>
        </AnimatePresence>

        <div className="mt-6 flex items-center justify-center gap-1.5 text-xs text-slate-400">
          <ShieldCheck className="h-3.5 w-3.5 text-emerald-600/70" />
          256-bit Bank-grade Encryption
        </div>
      </div>
    </motion.div>
  );
}
