"use client"

// Phase 10.5: listens for uncaught errors / unhandled promise rejections and
// sends a content-free report (see clientErrors.ts). Rendered by the root
// layout only when NEXT_PUBLIC_CLIENT_ERROR_REPORTING_ENABLED=true.

import { useEffect } from "react"

import { api } from "@/lib/api"

import { buildReport, MAX_REPORTS_PER_PAGE, type ClientErrorKind } from "./clientErrors"

let sent = 0

export function sendClientError(kind: ClientErrorKind, error: unknown, digest?: string): void {
	if (sent >= MAX_REPORTS_PER_PAGE || typeof window === "undefined") return
	sent += 1
	const report = buildReport(
		kind,
		error,
		window.location.pathname,
		process.env.NEXT_PUBLIC_RELEASE,
		digest,
	)
	// Best effort: a failed report must never surface to the user.
	api.post("/client-errors", { ...report }).catch(() => undefined)
}

export default function ClientErrorReporter() {
	useEffect(() => {
		const onError = (event: ErrorEvent) => sendClientError("error", event.error)
		const onRejection = (event: PromiseRejectionEvent) =>
			sendClientError("unhandledrejection", event.reason)
		window.addEventListener("error", onError)
		window.addEventListener("unhandledrejection", onRejection)
		return () => {
			window.removeEventListener("error", onError)
			window.removeEventListener("unhandledrejection", onRejection)
		}
	}, [])
	return null
}
