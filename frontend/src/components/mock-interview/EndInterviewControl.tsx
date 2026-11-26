"use client"

/**
 * Phase 6.6 (accessibility): the "End interview" control with its inline
 * confirmation, shared by the text/voice room and the video room. Same look
 * and texts as before; adds focus management and Escape:
 *   - opening the confirmation moves focus to "Keep going" (the safe choice),
 *   - Escape or "Keep going" closes it and returns focus to "End interview",
 *   - the two buttons are grouped and named for screen readers.
 */

import { useEffect, useRef, useState } from "react"

export default function EndInterviewControl({
	busy,
	ending,
	onConfirm,
}: {
	/** Any action in progress: both buttons are disabled. */
	busy: boolean
	/** The end request itself is in progress ("Ending…"). */
	ending: boolean
	/** Ends the interview; the confirmation closes when it settles. */
	onConfirm: () => void | Promise<unknown>
}) {
	const [open, setOpen] = useState(false)
	const keepRef = useRef<HTMLButtonElement>(null)
	const endRef = useRef<HTMLButtonElement>(null)
	const returnFocus = useRef(false)

	useEffect(() => {
		if (open) keepRef.current?.focus()
		else if (returnFocus.current) {
			returnFocus.current = false
			endRef.current?.focus()
		}
	}, [open])

	function close() {
		returnFocus.current = true
		setOpen(false)
	}

	function confirm() {
		void Promise.resolve(onConfirm()).finally(() => setOpen(false))
	}

	if (!open)
		return (
			<button
				ref={endRef}
				type="button"
				onClick={() => setOpen(true)}
				disabled={busy}
				className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs text-slate-600 hover:border-red-300 hover:text-red-600 disabled:opacity-50 dark:border-white/10 dark:text-slate-300"
			>
				End interview
			</button>
		)

	return (
		<span
			role="group"
			aria-label="Confirm ending the interview"
			className="flex items-center gap-2"
			onKeyDown={(e) => {
				if (e.key === "Escape") {
					e.preventDefault()
					close()
				}
			}}
		>
			<span className="text-xs text-slate-500">End now?</span>
			<button
				type="button"
				onClick={confirm}
				disabled={busy}
				className="rounded-lg bg-red-600 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
			>
				{ending ? "Ending…" : "Yes, end"}
			</button>
			<button
				ref={keepRef}
				type="button"
				onClick={close}
				className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs dark:border-white/10"
			>
				Keep going
			</button>
		</span>
	)
}
