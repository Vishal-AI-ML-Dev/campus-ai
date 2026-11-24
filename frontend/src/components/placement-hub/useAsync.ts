"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { ApiError } from "@/lib/api"

export type AsyncState<T> = {
	data: T | null
	loading: boolean
	error: string | null
	requestId?: string
	reload: () => void
}

/**
 * Run `fn` whenever `key` changes (pass null to wait). Responses that arrive
 * after a newer request started are ignored, so fast filter changes can never
 * show stale rows.
 */
export function useAsync<T>(key: string | null, fn: () => Promise<T>): AsyncState<T> {
	const [data, setData] = useState<T | null>(null)
	const [loading, setLoading] = useState<boolean>(key !== null)
	const [error, setError] = useState<string | null>(null)
	const [requestId, setRequestId] = useState<string | undefined>()
	const [tick, setTick] = useState(0)
	const seq = useRef(0)
	const fnRef = useRef(fn)
	useEffect(() => {
		fnRef.current = fn
	})
	const reload = useCallback(() => setTick((t) => t + 1), [])

	useEffect(() => {
		if (key === null) return
		const mine = ++seq.current
		setLoading(true)
		setError(null)
		fnRef
			.current()
			.then((res) => {
				if (mine === seq.current) setData(res)
			})
			.catch((e) => {
				if (mine !== seq.current) return
				setError(e instanceof ApiError ? e.message : "Something went wrong. Please try again.")
				setRequestId(e instanceof ApiError ? e.requestId : undefined)
			})
			.finally(() => {
				if (mine === seq.current) setLoading(false)
			})
	}, [key, tick])

	return { data, loading, error, requestId, reload }
}
