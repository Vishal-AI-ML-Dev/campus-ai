"use client"

// Error boundary for the /dashboard/* segment. Next.js renders this (inside the
// persistent AppShell) when a dashboard page throws during render, so a single
// broken page never blanks the whole app. `reset()` retries the segment.

import { useEffect } from "react"
import Link from "next/link"

export default function DashboardError({
	error,
	reset,
}: {
	error: Error & { digest?: string }
	reset: () => void
}) {
	useEffect(() => {
		console.error("Dashboard route error:", error)
	}, [error])

	return (
		<div className="mx-auto max-w-lg p-8 text-center">
			<h2 className="text-lg font-semibold text-gray-900 dark:text-slate-100">
				Something went wrong
			</h2>
			<p className="mt-2 text-sm text-gray-500 dark:text-slate-400">
				This page hit an unexpected error. You can try again, or head back to
				your dashboard.
			</p>
			{error.digest ? (
				<p className="mt-1 text-xs text-gray-400 dark:text-slate-500">
					Reference: <code>{error.digest}</code>
				</p>
			) : null}
			<div className="mt-4 flex justify-center gap-3">
				<button
					onClick={reset}
					className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white"
				>
					Try again
				</button>
				<Link
					href="/dashboard"
					className="rounded-md border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 dark:border-white/15 dark:text-slate-300"
				>
					Back to dashboard
				</Link>
			</div>
		</div>
	)
}
