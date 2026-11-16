/**
 * Campus AI - accept tenant invite page.
 *
 * Users open /accept-invite?token=... from an admin/TPO invite, set their
 * display name + password, then get logged in automatically.
 */

"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import ThemeToggle from "@/components/ThemeToggle";

type TokenResponse = { access_token: string; token_type: string };

export default function AcceptInvitePage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-slate-50 px-6 text-slate-900 dark:bg-slate-950 dark:text-slate-100">
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Loading invite...
          </p>
        </div>
      }
    >
      <AcceptInviteForm />
    </Suspense>
  );
}

function AcceptInviteForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const token = searchParams.get("token") ?? "";

  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!token) {
      setError("Invite token is missing. Ask your admin for a fresh invite link.");
      return;
    }
    setError(null);
    setLoading(true);
    try {
      // Backend auto-logs in by setting httpOnly cookies on success.
      await api.post<TokenResponse>("/tenants/accept-invite", {
        token,
        full_name: fullName,
        password,
      });
      router.push("/dashboard");
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Could not accept this invite. Ask your admin for a fresh link.",
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center bg-slate-50 px-6 text-slate-900 dark:bg-slate-950 dark:text-slate-100">
      <div className="absolute right-4 top-4">
        <ThemeToggle />
      </div>
      <div className="w-full max-w-sm">
        <Link
          href="/"
          className="mb-8 flex items-center justify-center gap-2 font-semibold"
        >
          <span className="grid h-9 w-9 place-items-center rounded-lg bg-gradient-to-br from-indigo-500 to-violet-500 text-sm font-bold">
            C
          </span>
          <span className="text-xl">Campus AI</span>
        </Link>

        <div className="rounded-2xl border border-slate-200 bg-white p-8 shadow-sm dark:border-white/10 dark:bg-slate-900">
          <h1 className="text-2xl font-bold">Accept invite</h1>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
            Set your name and password to join your institution workspace.
          </p>

          {!token && (
            <p className="mt-4 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-600 dark:text-red-300">
              Invite token is missing from the link.
            </p>
          )}

          <form onSubmit={handleSubmit} className="mt-6 space-y-4">
            <div>
              <label
                htmlFor="fullName"
                className="block text-sm font-medium text-slate-600 dark:text-slate-300"
              >
                Full name
              </label>
              <input
                id="fullName"
                required
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-indigo-400 dark:border-white/10 dark:bg-slate-900"
                placeholder="Asha Verma"
              />
            </div>

            <div>
              <label
                htmlFor="password"
                className="block text-sm font-medium text-slate-600 dark:text-slate-300"
              >
                Password
              </label>
              <input
                id="password"
                type="password"
                autoComplete="new-password"
                required
                minLength={6}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-indigo-400 dark:border-white/10 dark:bg-slate-900"
                placeholder="Minimum 6 characters"
              />
            </div>

            {error && (
              <p className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-600 dark:text-red-300">
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={loading || !token}
              className="w-full rounded-lg bg-gradient-to-r from-indigo-500 to-violet-500 px-4 py-2.5 font-medium text-white transition hover:opacity-90 disabled:opacity-50"
            >
              {loading ? "Joining..." : "Join workspace"}
            </button>
          </form>
        </div>

        <p className="mt-6 text-center text-sm text-slate-500 dark:text-slate-400">
          Already accepted?{" "}
          <Link
            href="/login"
            className="font-medium text-indigo-600 hover:text-indigo-500 dark:text-indigo-300"
          >
            Sign in
          </Link>
        </p>
      </div>
    </div>
  );
}
