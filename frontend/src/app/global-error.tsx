"use client"

// Last-resort error boundary: catches errors thrown in the ROOT layout itself.
// It replaces the whole document, so it must render its own <html>/<body>.

export default function GlobalError({
	error,
	reset,
}: {
	error: Error & { digest?: string }
	reset: () => void
}) {
	return (
		<html lang="en">
			<body className="flex min-h-screen items-center justify-center bg-slate-50 p-8 text-slate-900">
				<div className="max-w-lg text-center">
					<h2 className="text-lg font-semibold">Something went wrong</h2>
					<p className="mt-2 text-sm text-gray-600">
						The app hit an unexpected error. Please reload the page.
					</p>
					{error.digest ? (
						<p className="mt-1 text-xs text-gray-400">
							Reference: {error.digest}
						</p>
					) : null}
					<button
						onClick={reset}
						className="mt-4 rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white"
					>
						Reload
					</button>
				</div>
			</body>
		</html>
	)
}
