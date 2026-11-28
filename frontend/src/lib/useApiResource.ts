"use client"

// A tiny data-fetching hook that gives every list/detail view the SAME
// loading / error / reload behaviour, and surfaces the backend request id
// (via ApiError) so errors are traceable. This replaces the hand-rolled
// useState(loading/error) + useEffect(fetch) block that was copy-pasted into
// almost every panel.

import { useCallback, useEffect, useRef, useState } from "react"
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
 *
 * Only the LATEST request may update the state: a slower, superseded response
 * (path changed, a quick retry, or the component unmounted) is ignored, so it
 * can never overwrite newer data or leave `loading` stuck. Data belonging to a
 * previous path is cleared when the path changes; a reload of the same path
 * keeps the current data on screen until the new response arrives.
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
	// Sequence number of the newest request; older responses are dropped.
	const latest = useRef({ n: 0 })
	// The path the current `data` belongs to.
	const dataPath = useRef<string | null>(null)

	const reload = useCallback(() => {
		const id = ++latest.current.n
		if (dataPath.current !== path) {
			dataPath.current = path
			setData(null)
		}
		if (path === null) {
			setLoading(false)
			setError(null)
			setRequestId(undefined)
			return
		}
		setLoading(true)
		setError(null)
		setRequestId(undefined)
		api
			.get(path)
			.then((res) => {
				if (id === latest.current.n) setData(res as T)
			})
			.catch((e) => {
				if (id !== latest.current.n) return
				setError(e instanceof ApiError ? e.message : fallbackMessage)
				setRequestId(e instanceof ApiError ? e.requestId : undefined)
			})
			.finally(() => {
				if (id === latest.current.n) setLoading(false)
			})
	}, [path, fallbackMessage])

	useEffect(() => {
		reload()
		const counter = latest.current
		return () => {
			// Path changed or unmounted: any response still in flight is stale.
			counter.n++
		}
	}, [reload])

	return { data, loading, error, requestId, reload }
}
