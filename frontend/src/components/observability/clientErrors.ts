// Phase 10.5: content-free browser error reports (frontend error tracking).
//
// Only an error KIND, an error CLASS NAME, a normalised ROUTE (ids replaced,
// no query string or fragment), the build RELEASE and a Next.js DIGEST are
// ever sent. Error messages and stack traces are NEVER read into a report -
// they can contain student data. Off unless the build flag
// NEXT_PUBLIC_CLIENT_ERROR_REPORTING_ENABLED is exactly "true" (and the
// backend's CLIENT_ERROR_REPORTING_ENABLED is on; otherwise the POST is 404).

export type ClientErrorKind = "error" | "unhandledrejection" | "render"

export interface ClientErrorReport {
	kind: ClientErrorKind
	name: string
	route: string
	release: string
	digest: string
}

/** At most this many reports per page load (no flooding on a render loop). */
export const MAX_REPORTS_PER_PAGE = 5

export function reportingEnabled(flag: string | undefined): boolean {
	return flag === "true"
}

const ID_LIKE = /^(\d+|[0-9a-fA-F-]{16,})$/
const SEGMENT = /^[A-Za-z0-9_-]{1,40}$/
const NAME = /^[A-Za-z_][A-Za-z0-9_.]{0,59}$/

export function normaliseRoute(path: string): string {
	const clean = (path || "/").split("?")[0].split("#")[0]
	const parts: string[] = []
	for (const seg of clean.split("/")) {
		if (!seg) continue
		if (ID_LIKE.test(seg)) parts.push(":id")
		else if (SEGMENT.test(seg)) parts.push(seg.toLowerCase())
		else parts.push(":x")
		if (parts.length >= 8) break
	}
	return "/" + parts.join("/")
}

/** The error's class name only (e.g. "TypeError"); never its message. */
export function errorName(value: unknown): string {
	const name =
		value instanceof Error
			? value.name || value.constructor?.name
			: typeof value === "object" && value !== null
				? (value as { constructor?: { name?: string } }).constructor?.name
				: typeof value
	return typeof name === "string" && NAME.test(name) ? name : "Error"
}

export function buildReport(
	kind: ClientErrorKind,
	error: unknown,
	pathname: string,
	release: string | undefined,
	digest?: string,
): ClientErrorReport {
	return {
		kind,
		name: errorName(error),
		route: normaliseRoute(pathname),
		release: /^[A-Za-z0-9._-]{0,40}$/.test(release ?? "") ? (release ?? "") : "",
		digest: /^[A-Za-z0-9]{0,40}$/.test(digest ?? "") ? (digest ?? "") : "",
	}
}
