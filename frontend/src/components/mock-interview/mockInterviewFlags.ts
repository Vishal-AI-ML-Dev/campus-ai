/**
 * AI Mock Interview feature flag (frontend side).
 *
 * The feature is OFF unless the build sets NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED
 * to "true". When off, nothing new is fetched or shown: the sidebar is exactly
 * as before and /dashboard/mock-interview only shows a "not available" notice.
 * When on, the backend still decides per institute and role via
 * GET /mock-interview/status (AI_MOCK_INTERVIEW_ENABLED + optional tenant list).
 */

import type { NavItem, Role } from "@/lib/auth"
import { useApiResource } from "@/lib/useApiResource"

export const MOCK_INTERVIEW_BUILD_ENABLED =
	process.env.NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED === "true"

export type MockInterviewStatus = { enabled: boolean }

export const MOCK_INTERVIEW_NAV_ITEM: NavItem = {
	label: "Mock Interview",
	href: "/dashboard/mock-interview",
	icon: "\u{1F3A4}",
}

/**
 * The sidebar entry for this user, or null. Phase 1: students only.
 * Makes no request at all unless the build flag is on.
 */
export function useMockInterviewNavItem(role: Role | undefined): NavItem | null {
	const shouldAsk = MOCK_INTERVIEW_BUILD_ENABLED && role === "student"
	const { data } = useApiResource<MockInterviewStatus>(
		shouldAsk ? "/mock-interview/status" : null,
	)
	return shouldAsk && data?.enabled ? MOCK_INTERVIEW_NAV_ITEM : null
}
