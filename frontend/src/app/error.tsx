"use client"

// Error boundary for non-dashboard routes (marketing, login, etc.). Catches
// render-time errors so visitors see a friendly recovery screen instead of a
// blank page. `reset()` retries the segment.

import { useEffect } from "react"
import Link from "next/link"

export default function AppError({
	error,
	reset,
}: {
	error: Error & { digest?: string }
	reset: () => void
}) {
	useEffect(() => {
		console.error("Route error:", error)
	}, [error])

	return (
		<div className="flex min-h-screen items-center justify-center p-8">
			<div className="max-w-lg text-center">
				<h2 className="text-lg font-semibold text-gray-900 dark:text-slate-100">
					Something went wrong
				</h2>
				<p className="mt-2 text-sm text-gray-500 dark:text-slate-400">
					The page hit an unexpected error. Please try again.
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
						href="/"
						className="rounded-md border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 dark:border-white/15 dark:text-slate-300"
					>
						Go home
					</Link>
				</div>
			</div>
		</div>
	)
}
