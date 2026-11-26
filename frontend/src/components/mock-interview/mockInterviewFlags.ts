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
import { VIDEO_UI_READY, videoBuildEnabled } from "./mockInterviewModes"
import { codingBuildEnabled } from "./coding/codingFlow"

export const MOCK_INTERVIEW_BUILD_ENABLED =
	process.env.NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED === "true"

/**
 * Phase 6 video build flag (default OFF). Needs the master build flag too.
 * Literal process.env reads so Next inlines them at build time. The backend
 * still decides per institute/role (AI_MOCK_INTERVIEW_VIDEO_* flags), and
 * next.config.ts allows the camera only on the mock-interview route when
 * this is on.
 */
export const MOCK_INTERVIEW_VIDEO_BUILD_ENABLED = videoBuildEnabled({
	NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED: process.env.NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED,
	NEXT_PUBLIC_MOCK_INTERVIEW_VIDEO_ENABLED: process.env.NEXT_PUBLIC_MOCK_INTERVIEW_VIDEO_ENABLED,
})

/**
 * Phase 8.2 coding build flag (default OFF). Needs the master build flag too.
 * The backend still decides per institute (AI_MOCK_INTERVIEW_CODING_* flags)
 * and only offers "coding" in /options when its gates pass.
 */
export const MOCK_INTERVIEW_CODING_BUILD_ENABLED = codingBuildEnabled({
	NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED: process.env.NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED,
	NEXT_PUBLIC_MOCK_INTERVIEW_CODING_ENABLED: process.env.NEXT_PUBLIC_MOCK_INTERVIEW_CODING_ENABLED,
})

/** Video may be offered in the setup form (build flag AND the 6.3 UI exists). */
export const MOCK_INTERVIEW_VIDEO_AVAILABLE = MOCK_INTERVIEW_VIDEO_BUILD_ENABLED && VIDEO_UI_READY

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
