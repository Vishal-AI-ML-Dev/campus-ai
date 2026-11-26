/**
 * Campus AI - student self-registration (request to join an institute).
 *
 * POST /auth/register creates a STUDENT account that is PENDING: it cannot
 * sign in until an admin of that institute approves it on the Users page.
 * No session is started here and no email is sent (there is no email
 * verification), so the page tells the student exactly what happens next.
 */

"use client"

import { useState } from "react"
import Link from "next/link"
import { api, ApiError } from "@/lib/api"
import ThemeToggle from "@/components/ThemeToggle"

type RegisterResponse = {
	id: number
	email: string
	full_name: string
	role: string
	is_active: boolean
}

const inputClass =
	"mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-indigo-400 dark:border-white/10 dark:bg-slate-900"

function normaliseCode(value: string): string {
	return value.toLowerCase().replace(/[^a-z0-9-]/g, "").slice(0, 63)
}

export default function RegisterPage() {
	const [institutionCode, setInstitutionCode] = useState("")
	const [fullName, setFullName] = useState("")
	const [email, setEmail] = useState("")
	const [password, setPassword] = useState("")
	const [confirm, setConfirm] = useState("")
	const [error, setError] = useState<string | null>(null)
	const [loading, setLoading] = useState(false)
	const [pending, setPending] = useState<RegisterResponse | null>(null)

	async function handleSubmit(e: React.FormEvent) {
		e.preventDefault()
		setError(null)
		if (password !== confirm) {
			setError("The two passwords do not match.")
			return
		}
		setLoading(true)
		try {
			const created = await api.post<RegisterResponse>("/auth/register", {
				tenant_slug: institutionCode,
				full_name: fullName,
				email,
				password,
			})
			setPending(created)
			setPassword("")
			setConfirm("")
		} catch (err) {
			if (err instanceof ApiError && err.status === 404) {
				setError("No active institute uses that code. Check it with your institute admin.")
			} else if (err instanceof ApiError && err.status === 409) {
				setError("An account with this email already exists. Try signing in instead.")
			} else {
				setError(err instanceof ApiError ? err.message : "Could not reach the server. Please try again.")
			}
		} finally {
			setLoading(false)
		}
	}

	return (
		<div className="relative flex min-h-screen items-center justify-center bg-slate-50 px-6 py-10 text-slate-900 dark:bg-slate-950 dark:text-slate-100">
			<div className="absolute right-4 top-4"><ThemeToggle /></div>
			<div className="w-full max-w-md">
				<Link href="/" className="mb-8 flex items-center justify-center gap-2 font-semibold">
					<span className="grid h-9 w-9 place-items-center rounded-lg bg-gradient-to-br from-indigo-500 to-violet-500 text-sm font-bold">
						C
					</span>
					<span className="text-xl">Campus AI</span>
				</Link>

				{pending ? (
					<div
						role="status"
						className="rounded-2xl border border-amber-400/40 bg-white p-8 shadow-sm dark:bg-slate-900"
					>
						<p className="text-xs font-semibold uppercase tracking-wide text-amber-600 dark:text-amber-300">
							Pending approval
						</p>
						<h1 className="mt-2 text-2xl font-bold">Request sent</h1>
						<p className="mt-3 text-sm text-slate-600 dark:text-slate-300">
							Your student account for <span className="font-medium">{pending.email}</span> has been
							created, but it is <span className="font-medium">not active yet</span>. An admin of your
							institute must approve it before you can sign in.
						</p>
						<ul className="mt-4 list-disc space-y-1 pl-5 text-sm text-slate-600 dark:text-slate-300">
							<li>Campus AI does not send emails, so you will not receive a confirmation message.</li>
							<li>Ask your institute admin to approve your request on their Users page.</li>
							<li>Once approved, sign in with the email and password you just chose.</li>
						</ul>
						<Link
							href="/login"
							className="mt-6 block w-full rounded-lg bg-gradient-to-r from-indigo-500 to-violet-500 px-4 py-2.5 text-center font-medium text-white transition hover:opacity-90"
						>
							Go to sign in
						</Link>
					</div>
				) : (
					<div className="rounded-2xl border border-slate-200 bg-white p-8 shadow-sm dark:border-white/10 dark:bg-slate-900">
						<h1 className="text-2xl font-bold">Join your institute</h1>
						<p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
							Create a student account. It stays pending until an admin of your institute approves it.
						</p>

						<form onSubmit={handleSubmit} className="mt-6 space-y-4">
							<div>
								<label htmlFor="institutionCode" className="block text-sm font-medium text-slate-600 dark:text-slate-300">Institute code</label>
								<input id="institutionCode" required pattern="[a-z0-9-]+" minLength={2} maxLength={63} value={institutionCode} onChange={(e) => setInstitutionCode(normaliseCode(e.target.value))} className={inputClass} placeholder="acme-institute" autoComplete="off" />
								<p className="mt-1 text-xs text-slate-500 dark:text-slate-400">Your institute admin can give you this code.</p>
							</div>

							<div>
								<label htmlFor="fullName" className="block text-sm font-medium text-slate-600 dark:text-slate-300">Full name</label>
								<input id="fullName" required maxLength={255} value={fullName} onChange={(e) => setFullName(e.target.value)} className={inputClass} placeholder="Asha Verma" autoComplete="name" />
							</div>

							<div>
								<label htmlFor="email" className="block text-sm font-medium text-slate-600 dark:text-slate-300">Email</label>
								<input id="email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} className={inputClass} placeholder="you@college.edu" autoComplete="email" />
							</div>

							<div className="grid gap-4 sm:grid-cols-2">
								<div>
									<label htmlFor="password" className="block text-sm font-medium text-slate-600 dark:text-slate-300">Password</label>
									<input id="password" type="password" required minLength={6} maxLength={128} value={password} onChange={(e) => setPassword(e.target.value)} className={inputClass} placeholder="Minimum 6 characters" autoComplete="new-password" />
								</div>
								<div>
									<label htmlFor="confirm" className="block text-sm font-medium text-slate-600 dark:text-slate-300">Confirm password</label>
									<input id="confirm" type="password" required minLength={6} maxLength={128} value={confirm} onChange={(e) => setConfirm(e.target.value)} className={inputClass} autoComplete="new-password" />
								</div>
							</div>

							{error && (
								<p role="alert" className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-600 dark:text-red-300">
									{error}
								</p>
							)}

							<button type="submit" disabled={loading} className="w-full rounded-lg bg-gradient-to-r from-indigo-500 to-violet-500 px-4 py-2.5 font-medium text-white transition hover:opacity-90 disabled:opacity-50">
								{loading ? "Sending request..." : "Request access"}
							</button>
						</form>
					</div>
				)}

				<p className="mt-6 text-center text-sm text-slate-500 dark:text-slate-400">
					Already approved?{" "}
					<Link href="/login" className="font-medium text-indigo-600 hover:text-indigo-500 dark:text-indigo-300">Sign in</Link>
					{" · "}
					New college?{" "}
					<Link href="/create-institution" className="font-medium text-indigo-600 hover:text-indigo-500 dark:text-indigo-300">Create your institution</Link>
				</p>
			</div>
		</div>
	)
}
