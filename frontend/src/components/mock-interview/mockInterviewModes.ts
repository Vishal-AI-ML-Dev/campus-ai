/**
 * Which interview modes the setup form may offer (pure; unit-tested in Node).
 *
 * The backend decides what a student may use (GET /mock-interview/options
 * lists "video" only when every server-side gate passes) and enforces it on
 * POST /sessions. The frontend only ever narrows that list:
 *
 *   - "video" needs the build flag NEXT_PUBLIC_MOCK_INTERVIEW_VIDEO_ENABLED
 *     (on top of NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED), AND the video lobby/room
 *     to exist. Phase 6.1/6.2 kept VIDEO_UI_READY false; Phase 6.3 added the
 *     lobby and room (VideoInterviewWorkspace) and set it to true. Video is
 *     still OFF unless the build flag AND the server gates allow it.
 *   - "text" and "voice" pass through unchanged (Phase 3/5 behaviour).
 */

export type ModeChoice = { value: string; label: string }

/** The camera lobby and video room exist (Phase 6.3). Build flag and server gates still apply. */
export const VIDEO_UI_READY = true

/** True only when BOTH build flags are exactly "true". Default: false. */
export function videoBuildEnabled(env: {
	NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED?: string
	NEXT_PUBLIC_MOCK_INTERVIEW_VIDEO_ENABLED?: string
}): boolean {
	return (
		env.NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED === "true" &&
		env.NEXT_PUBLIC_MOCK_INTERVIEW_VIDEO_ENABLED === "true"
	)
}

/** The server's mode list minus anything this build cannot show. */
export function visibleModes(modes: ModeChoice[] | undefined, videoAvailable: boolean): ModeChoice[] {
	return (modes ?? []).filter((m) => m.value !== "video" || videoAvailable)
}
