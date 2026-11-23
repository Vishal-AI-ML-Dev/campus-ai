/** Phase 8.2: what review-only mode means (decision P8-D8). Shown before and during a round. */

export default function ReviewOnlyNotice({ compact = false }: { compact?: boolean }) {
	return (
		<div
			role="note"
			className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-800 dark:text-amber-200"
		>
			<p className="font-medium">Review-only practice: your code is saved but not run.</p>
			{compact ? null : (
				<p className="mt-1">
					Running code and test results are not available yet, so there is no Run button and no score for
					your code. Use the examples and sample tests to check your logic by hand. Feedback for coding rounds
					will be added in a later update.
				</p>
			)}
		</div>
	)
}
