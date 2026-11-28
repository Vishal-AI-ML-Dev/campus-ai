"use client"

/**
 * Phase 9 closure (L2): Shortlist / Select / Reject row for the legacy TPO
 * applicant pages. Rules live in `lib/legacyApplicationActions.ts`.
 *
 * Withdrawn rows keep every button visible but natively `disabled` (not
 * focusable, not activatable by mouse, Enter or Space) and point to a visible
 * note via `aria-describedby`, so the reason is announced. The parent's
 * `onDecide` is also never called for a withdrawn row.
 */

import {
	LEGACY_DECISIONS,
	WITHDRAWN_FINAL_NOTE,
	isWithdrawn,
	legacyDecisionDisabled,
	type LegacyAppStatus,
	type LegacyDecision,
} from "@/lib/legacyApplicationActions"

const LABELS: Record<LegacyDecision, string> = {
	shortlisted: "Shortlist",
	selected: "Select",
	rejected: "Reject",
}

const CLASSES: Record<LegacyDecision, string> = {
	shortlisted:
		"rounded-lg border border-amber-400/30 px-3 py-1.5 text-sm text-amber-600 dark:text-amber-300 transition hover:bg-amber-400/10 disabled:cursor-not-allowed disabled:opacity-40",
	selected:
		"rounded-lg border border-emerald-400/30 px-3 py-1.5 text-sm text-emerald-600 dark:text-emerald-300 transition hover:bg-emerald-400/10 disabled:cursor-not-allowed disabled:opacity-40",
	rejected:
		"rounded-lg border border-red-400/30 px-3 py-1.5 text-sm text-red-700 dark:text-red-300 transition hover:bg-red-400/10 disabled:cursor-not-allowed disabled:opacity-40",
}

export default function LegacyDecisionButtons({
	applicationId,
	status,
	onDecide,
}: {
	applicationId: number
	status: LegacyAppStatus
	onDecide: (target: LegacyDecision) => void
}) {
	const withdrawn = isWithdrawn(status)
	const noteId = `withdrawn-note-${applicationId}`
	return (
		<div className="mt-3">
			<div className="flex flex-wrap gap-2">
				{LEGACY_DECISIONS.map((target) => (
					<button
						key={target}
						type="button"
						onClick={() => {
							if (!legacyDecisionDisabled(status, target)) onDecide(target)
						}}
						disabled={legacyDecisionDisabled(status, target)}
						aria-describedby={withdrawn ? noteId : undefined}
						className={CLASSES[target]}
					>
						{LABELS[target]}
					</button>
				))}
			</div>
			{withdrawn && (
				<p id={noteId} className="mt-2 text-xs text-slate-500 dark:text-slate-400">
					{WITHDRAWN_FINAL_NOTE}
				</p>
			)}
		</div>
	)
}
