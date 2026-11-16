"use client"

// A tiny data-fetching hook that gives every list/detail view the SAME
// loading / error / reload behaviour, and surfaces the backend request id
// (via ApiError) so errors are traceable. This replaces the hand-rolled
// useState(loading/error) + useEffect(fetch) block that was copy-pasted into
// almost every panel.

import { useCallback, useEffect, useState } from "react"
import { api, ApiError } from "@/lib/api"

export type ApiResource<T> = {
	data: T | null
	loading: boolean
	error: string | null
	requestId?: string
	reload: () => void
}

/**
 * GET a resource with consistent loading/error/reload handling.
 *
 * Pass `null` as the path to skip fetching until a prerequisite is ready
 * (e.g. a section has been picked). `reload()` re-runs the request.
 */
export function useApiResource<T>(
	path: string | null,
	options?: { fallbackMessage?: string },
): ApiResource<T> {
	const fallbackMessage =
		options?.fallbackMessage ?? "Something went wrong. Please try again."
	const [data, setData] = useState<T | null>(null)
	const [loading, setLoading] = useState<boolean>(path !== null)
	const [error, setError] = useState<string | null>(null)
	const [requestId, setRequestId] = useState<string | undefined>(undefined)

	const reload = useCallback(() => {
		if (path === null) return
		setLoading(true)
		setError(null)
		setRequestId(undefined)
		api
			.get(path)
			.then((res) => setData(res as T))
			.catch((e) => {
				setError(e instanceof ApiError ? e.message : fallbackMessage)
				setRequestId(e instanceof ApiError ? e.requestId : undefined)
			})
			.finally(() => setLoading(false))
	}, [path, fallbackMessage])

	useEffect(() => {
		reload()
	}, [reload])

	return { data, loading, error, requestId, reload }
}
