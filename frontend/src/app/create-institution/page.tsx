/**
 * Campus AI - self-serve institution onboarding page.
 *
 * Creates a new tenant (institution) plus its first admin account through
 * POST /tenants/signup, stores the returned JWT, and opens the dashboard.
 */

"use client"

import { useMemo, useState } from "react"
import { useRouter } from "next/navigation"
import Link from "next/link"
import { api, ApiError } from "@/lib/api"
import ThemeToggle from "@/components/ThemeToggle"

type SignupResponse = { access_token: string; token_type: string }

function slugify(value: string): string {
	return value
		.toLowerCase()
		.trim()
		.replace(/[^a-z0-9]+/g, "-")
		.replace(/^-+|-+$/g, "")
		.slice(0, 63)
}

// While the user types in the slug box: keep only allowed characters but do
// NOT trim hyphens, otherwise "acme-" collapses to "acme" and a hyphen can
// never be typed. Edge hyphens are trimmed on submit by slugify().
function normaliseSlugInput(value: string): string {
	return value.toLowerCase().replace(/\s+/g, "-").replace(/[^a-z0-9-]/g, "").slice(0, 63)
}

export default function CreateInstitutionPage() {
	const router = useRouter()
	const [institutionName, setInstitutionName] = useState("")
	const [slugInput, setSlugInput] = useState("")
	// The slug follows the institution name until the user edits it by hand.
	const [slugEdited, setSlugEdited] = useState(false)
	const [adminFullName, setAdminFullName] = useState("")
	const [adminEmail, setAdminEmail] = useState("")
	const [adminPassword, setAdminPassword] = useState("")
	const [error, setError] = useState<string | null>(null)
	const [loading, setLoading] = useState(false)

	const suggestedSlug = useMemo(() => slugify(institutionName), [institutionName])
	const institutionSlug = slugEdited ? slugInput : suggestedSlug

	function handleSlugChange(value: string) {
		setSlugInput(normaliseSlugInput(value))
		// Once touched, the slug is the user's own (even when emptied, so it can
		// be cleared and retyped); `required` blocks submitting it empty.
		setSlugEdited(true)
	}

	async function handleSubmit(e: React.FormEvent) {
		e.preventDefault()
		setError(null)
		const finalSlug = slugify(institutionSlug)
		// The browser's minLength check skips values the page filled in itself,
		// so check here too (the backend rejects slugs under 2 characters).
		if (finalSlug.length < 2) {
			setError("Institution slug must be at least 2 characters (lowercase letters, numbers, hyphens).")
			return
		}
		setLoading(true)
		try {
			// Backend auto-logs in by setting httpOnly cookies on success.
			await api.post<SignupResponse>("/tenants/signup", {
				institution_name: institutionName.trim(),
				institution_slug: finalSlug,
				admin_full_name: adminFullName,
				admin_email: adminEmail,
				admin_password: adminPassword,
			})
			router.push("/dashboard")
		} catch (err) {
			setError(
				err instanceof ApiError
					? err.message
					: "Could not create the institution. Is the backend running?",
			)
		} finally {
			setLoading(false)
		}
	}

	return (
		<div className="relative flex min-h-screen items-center justify-center bg-slate-50 dark:bg-slate-950 px-6 py-10 text-slate-900 dark:text-slate-100">
			<div className="absolute right-4 top-4"><ThemeToggle /></div>
			<div className="w-full max-w-xl">
				<Link href="/" className="mb-8 flex items-center justify-center gap-2 font-semibold">
					<span className="grid h-9 w-9 place-items-center rounded-lg bg-gradient-to-br from-indigo-500 to-violet-500 text-sm font-bold">
						C
					</span>
					<span className="text-xl">Campus AI</span>
				</Link>

				<div className="rounded-2xl border border-slate-200 bg-white p-8 shadow-sm dark:border-white/10 dark:bg-slate-900">
					<h1 className="text-2xl font-bold">Create your institution</h1>
					<p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
						Set up a new college tenant and first admin account. You can import the roster after login.
					</p>

					<form onSubmit={handleSubmit} className="mt-6 space-y-4">
						<div className="grid gap-4 sm:grid-cols-2">
							<div className="sm:col-span-2">
								<label htmlFor="institutionName" className="block text-sm font-medium text-slate-600 dark:text-slate-300">Institution name</label>
								<input id="institutionName" required maxLength={255} value={institutionName} onChange={(e) => setInstitutionName(e.target.value)} className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-indigo-400 dark:border-white/10 dark:bg-slate-900" placeholder="Acme Institute of Technology" />
							</div>

							<div className="sm:col-span-2">
								<label htmlFor="institutionSlug" className="block text-sm font-medium text-slate-600 dark:text-slate-300">Institution slug</label>
								<input id="institutionSlug" required pattern="[a-z0-9\-]+" minLength={2} maxLength={63} value={institutionSlug} onChange={(e) => handleSlugChange(e.target.value)} className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-indigo-400 dark:border-white/10 dark:bg-slate-900" placeholder="acme-institute" />
								<p className="mt-1 text-xs text-slate-500 dark:text-slate-400">Lowercase letters, numbers, and hyphens only.</p>
							</div>

							<div>
								<label htmlFor="adminFullName" className="block text-sm font-medium text-slate-600 dark:text-slate-300">Admin full name</label>
								<input id="adminFullName" required maxLength={255} value={adminFullName} onChange={(e) => setAdminFullName(e.target.value)} className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-indigo-400 dark:border-white/10 dark:bg-slate-900" placeholder="Campus Admin" />
							</div>

							<div>
								<label htmlFor="adminEmail" className="block text-sm font-medium text-slate-600 dark:text-slate-300">Admin email</label>
								<input id="adminEmail" type="email" autoComplete="email" required value={adminEmail} onChange={(e) => setAdminEmail(e.target.value)} className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-indigo-400 dark:border-white/10 dark:bg-slate-900" placeholder="admin@college.edu" />
							</div>
						</div>

						<div>
							<label htmlFor="adminPassword" className="block text-sm font-medium text-slate-600 dark:text-slate-300">Admin password</label>
							<input id="adminPassword" type="password" autoComplete="new-password" required minLength={6} maxLength={128} value={adminPassword} onChange={(e) => setAdminPassword(e.target.value)} className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-indigo-400 dark:border-white/10 dark:bg-slate-900" placeholder="Minimum 6 characters" />
						</div>

						{error && (
							<p role="alert" className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-600 dark:text-red-300">
								{error}
							</p>
						)}

						<button type="submit" disabled={loading} className="w-full rounded-lg bg-gradient-to-r from-indigo-500 to-violet-500 px-4 py-2.5 font-medium text-white transition hover:opacity-90 disabled:opacity-50">
							{loading ? "Creating institution..." : "Create institution"}
						</button>
					</form>
				</div>

				<p className="mt-6 text-center text-sm text-slate-500 dark:text-slate-400">
					Already have an account?{" "}
					<Link href="/login" className="font-medium text-indigo-600 hover:text-indigo-500 dark:text-indigo-300">Sign in</Link>
				</p>
			</div>
		</div>
	)
}
