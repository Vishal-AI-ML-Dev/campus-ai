/**
 * Shown on /dashboard/mock-interview whenever the AI Mock Interview is not
 * available to this user (build flag off, or the backend says it is off for
 * their institute or role).
 */
export default function MockInterviewUnavailableNotice() {
	return (
		<div
			role="status"
			className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-800 dark:text-amber-200"
		>
			<p className="font-medium">AI Mock Interview is not available yet.</p>
			<p className="mt-1">
				Your institute has not turned on mock interviews. Other placement tools
				on your dashboard work as usual.
			</p>
		</div>
	)
}
