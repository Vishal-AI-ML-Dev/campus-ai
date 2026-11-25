/**
 * Phase 9 closure (L2): decision-button rules for the legacy TPO applicant
 * pages (`/dashboard/drives/[id]` and `/dashboard/applications`).
 *
 * The legacy rule is unchanged for every status except `withdrawn`: a button
 * is disabled only when it would set the status the application already has.
 * A withdrawal is made by the student and is final (P9-D8) — the server
 * refuses every staff change (409) — so all three decisions are disabled.
 */

export type LegacyAppStatus = "applied" | "shortlisted" | "selected" | "rejected" | "withdrawn"

export type LegacyDecision = "shortlisted" | "selected" | "rejected"

export const LEGACY_DECISIONS: readonly LegacyDecision[] = ["shortlisted", "selected", "rejected"]

export const WITHDRAWN_FINAL_NOTE =
	"Withdrawn by the student. Withdrawal is final, so no further decision can be made."

export function isWithdrawn(status: LegacyAppStatus): boolean {
	return status === "withdrawn"
}

export function legacyDecisionDisabled(current: LegacyAppStatus, target: LegacyDecision): boolean {
	return isWithdrawn(current) || current === target
}
