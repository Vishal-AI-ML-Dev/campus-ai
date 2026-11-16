/**
 * Campus AI - recruiter invite acceptance page.
 *
 * Path matches the backend's `accept_path` for recruiter invites:
 *   /recruiter/accept-invite?token=...
 *
 * The recruiter sets a display name + password; the backend creates the
 * account in the institute that issued the invite (the tenant is derived
 * server-side from the invite - nothing tenant-related is sent from here).
 *
 * Token handling:
 *   - read ONCE from the URL into component state, then removed from the
 *     visible URL/history entry (history.replaceState) so it is not left in
 *     the address bar, bookmarks or screenshots;
 *   - never logged, never written to storage;
 *   - the layout sets referrer "no-referrer" so it cannot leak via Referer.
 *
 * Unlike the institute invite, this endpoint does NOT start a session (it sets
 * no auth cookies), so on success the recruiter is sent to sign in.
 */

"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import ThemeToggle from "@/components/ThemeToggle";
import { Spinner } from "@/components/ui/states";

type Phase = "form" | "submitting" | "success";

export default function RecruiterAcceptInvitePage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-slate-50 px-6 text-slate-900 dark:bg-slate-950 dark:text-slate-100">
          <p role="status" className="text-sm text-slate-500 dark:text-slate-400">
            Loading invite...
          </p>
        </div>
      }
    >
      <RecruiterAcceptInviteForm />
    </Suspense>
  );
}

function RecruiterAcceptInviteForm() {
  const searchParams = useSearchParams();
  // Captured exactly once (lazy initial state); later URL changes - including
  // the replaceState below - never overwrite it.
  const [token] = useState<string>(() => searchParams.get("token") ?? "");
  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [phase, setPhase] = useState<Phase>("form");

  // Strip the token from the visible URL once it has been captured.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const url = new URL(window.location.href);
    if (url.searchParams.has("token")) {
      url.searchParams.delete("token");
      window.history.replaceState(
        window.history.state,
        "",
        url.pathname + (url.search ? url.search : "") + url.hash,
      );
    }
  }, []);

  const tokenMissing = token.trim().length === 0;
  const submitting = phase === "submitting";

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (tokenMissing) {
      setError(
        "This invite link is incomplete. Ask the placement office for a fresh invite.",
      );
      return;
    }
    if (password !== confirm) {
      setError("The two passwords do not match.");
      return;
    }
    setError(null);
    setPhase("submitting");
    try {
      // The response carries an access token in its body; it is deliberately
      // NOT stored - the app authenticates with httpOnly cookies, which this
      // endpoint does not set, so the recruiter signs in normally next.
      await api.post("/recruiters/accept-invite", {
        token,
        full_name: fullName.trim(),
        password,
      });
      setPassword("");
      setConfirm("");
      setPhase("success");
    } catch (err) {
      setPhase("form");
      setError(
        err instanceof ApiError
          ? friendlyError(err)
          : "Could not reach the server. Check your connection and try again.",
      );
    }
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center bg-slate-50 px-4 py-10 text-slate-900 sm:px-6 dark:bg-slate-950 dark:text-slate-100">
      <div className="absolute right-4 top-4">
        <ThemeToggle />
      </div>
      <main className="w-full max-w-sm">
        <Link
          href="/"
          className="mb-8 flex items-center justify-center gap-2 font-semibold"
        >
          <span
            aria-hidden="true"
            className="grid h-9 w-9 place-items-center rounded-lg bg-gradient-to-br from-indigo-500 to-violet-500 text-sm font-bold"
          >
            C
          </span>
          <span className="text-xl">Campus AI</span>
        </Link>

        <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm sm:p-8 dark:border-white/10 dark:bg-slate-900">
          {phase === "success" ? (
            <div role="status" aria-live="polite">
              <h1 className="text-2xl font-bold">Your recruiter account is ready</h1>
              <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">
                Sign in with the email address the invite was sent to and the
                password you just chose.
              </p>
              <Link
                href="/login"
                className="mt-6 block w-full rounded-lg bg-gradient-to-r from-indigo-500 to-violet-500 px-4 py-2.5 text-center font-medium text-white transition hover:opacity-90"
              >
                Continue to sign in
              </Link>
            </div>
          ) : (
            <>
              <h1 className="text-2xl font-bold">Join as a recruiter</h1>
              <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
                A placement office invited your company to Campus AI. Set your
                name and a password to create your recruiter account.
              </p>

              {tokenMissing && (
                <p
                  role="alert"
                  className="mt-4 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-600 dark:text-red-300"
                >
                  This invite link is missing its code. Open the full link from
                  your invite, or ask the placement office for a new one.
                </p>
              )}

              <form
                onSubmit={handleSubmit}
                className="mt-6 space-y-4"
                aria-busy={submitting}
              >
                <div>
                  <label
                    htmlFor="recruiter-full-name"
                    className="block text-sm font-medium text-slate-600 dark:text-slate-300"
                  >
                    Full name
                  </label>
                  <input
                    id="recruiter-full-name"
                    name="full_name"
                    required
                    maxLength={255}
                    autoComplete="name"
                    value={fullName}
                    onChange={(e) => setFullName(e.target.value)}
                    disabled={submitting || tokenMissing}
                    className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-indigo-400 disabled:opacity-60 dark:border-white/10 dark:bg-slate-900"
                  />
                </div>

                <div>
                  <label
                    htmlFor="recruiter-password"
                    className="block text-sm font-medium text-slate-600 dark:text-slate-300"
                  >
                    Password
                  </label>
                  <input
                    id="recruiter-password"
                    name="password"
                    type="password"
                    autoComplete="new-password"
                    required
                    minLength={6}
                    maxLength={128}
                    aria-describedby="recruiter-password-hint"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    disabled={submitting || tokenMissing}
                    className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-indigo-400 disabled:opacity-60 dark:border-white/10 dark:bg-slate-900"
                  />
                  <p
                    id="recruiter-password-hint"
                    className="mt-1 text-xs text-slate-500 dark:text-slate-400"
                  >
                    At least 6 characters.
                  </p>
                </div>

                <div>
                  <label
                    htmlFor="recruiter-password-confirm"
                    className="block text-sm font-medium text-slate-600 dark:text-slate-300"
                  >
                    Confirm password
                  </label>
                  <input
                    id="recruiter-password-confirm"
                    name="password_confirm"
                    type="password"
                    autoComplete="new-password"
                    required
                    minLength={6}
                    maxLength={128}
                    value={confirm}
                    onChange={(e) => setConfirm(e.target.value)}
                    disabled={submitting || tokenMissing}
                    className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-indigo-400 disabled:opacity-60 dark:border-white/10 dark:bg-slate-900"
                  />
                </div>

                {error && (
                  <p
                    role="alert"
                    className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-600 dark:text-red-300"
                  >
                    {error}
                  </p>
                )}

                <button
                  type="submit"
                  disabled={submitting || tokenMissing}
                  className="flex w-full items-center justify-center gap-2 rounded-lg bg-gradient-to-r from-indigo-500 to-violet-500 px-4 py-2.5 font-medium text-white transition hover:opacity-90 disabled:opacity-50"
                >
                  {submitting && <Spinner />}
                  {submitting ? "Creating account..." : "Create recruiter account"}
                </button>
              </form>
            </>
          )}
        </div>

        <p className="mt-6 text-center text-sm text-slate-500 dark:text-slate-400">
          Already have an account?{" "}
          <Link
            href="/login"
            className="font-medium text-indigo-600 hover:text-indigo-500 dark:text-indigo-300"
          >
            Sign in
          </Link>
        </p>
      </main>
    </div>
  );
}

/** Map backend responses to guidance a recruiter can act on. */
function friendlyError(err: ApiError): string {
  if (err.status === 409) {
    return "An account already exists for this email. Sign in instead, or ask the placement office to invite a different address.";
  }
  if (err.status === 429) {
    return "Too many attempts. Please wait a minute and try again.";
  }
  if (err.status === 400) {
    // "Invalid or already-used invite" / "This invite has expired".
    return `${err.message}. Ask the placement office for a new invite.`;
  }
  if (err.status === 422) {
    return "Please check the details: a name is required and the password needs at least 6 characters.";
  }
  return err.message;
}
