"use client"

// Topbar notification bell: shows an unread badge (polled), and a dropdown feed
// where the user can open an item (marks it read + deep-links) or mark all read.
// Reads its data from the backend /notifications endpoints via notificationsApi.

import { useCallback, useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import {
	fetchNotifications,
	fetchUnreadCount,
	markAllNotificationsRead,
	markNotificationRead,
	iconForType,
	timeAgo,
	type NotificationOut,
} from "@/components/notifications/notificationsApi"
import { LoadingState, ErrorState, EmptyState } from "@/components/ui/states"

// How often to refresh the unread badge while the app is open.
const POLL_MS = 60_000

export default function NotificationBell() {
	const router = useRouter()
	const [open, setOpen] = useState(false)
	const [unread, setUnread] = useState(0)
	const [items, setItems] = useState<NotificationOut[] | null>(null)
	const [loading, setLoading] = useState(false)
	const [error, setError] = useState<string | null>(null)
	const rootRef = useRef<HTMLDivElement>(null)

	// Poll the unread badge count on mount + every POLL_MS (best-effort).
	const refreshCount = useCallback(() => {
		fetchUnreadCount()
			.then((r) => setUnread(r.unread))
			.catch(() => {
				/* badge is best-effort; ignore transient errors */
			})
	}, [])

	useEffect(() => {
		refreshCount()
		const t = setInterval(refreshCount, POLL_MS)
		return () => clearInterval(t)
	}, [refreshCount])

	// Load the feed (called when the panel opens).
	const loadFeed = useCallback(() => {
		setLoading(true)
		setError(null)
		fetchNotifications()
			.then((data) => setItems(data))
			.catch((e) =>
				setError(
					e instanceof Error ? e.message : "Could not load notifications.",
				),
			)
			.finally(() => setLoading(false))
	}, [])

	// Close the dropdown on outside click / Escape.
	useEffect(() => {
		if (!open) return
		function onClick(e: MouseEvent) {
			if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
				setOpen(false)
			}
		}
		function onKey(e: KeyboardEvent) {
			if (e.key === "Escape") setOpen(false)
		}
		document.addEventListener("mousedown", onClick)
		document.addEventListener("keydown", onKey)
		return () => {
			document.removeEventListener("mousedown", onClick)
			document.removeEventListener("keydown", onKey)
		}
	}, [open])

	function toggle() {
		const next = !open
		setOpen(next)
		if (next) loadFeed()
	}

	function openItem(n: NotificationOut) {
		// Optimistically mark read, then deep-link to its target (if any).
		if (!n.is_read) {
			setItems(
				(cur) =>
					cur?.map((x) => (x.id === n.id ? { ...x, is_read: true } : x)) ?? cur,
			)
			setUnread((u) => Math.max(0, u - 1))
			markNotificationRead(n.id).catch(() => refreshCount())
		}
		setOpen(false)
		if (n.link) router.push(n.link)
	}

	async function markAll() {
		setItems((cur) => cur?.map((x) => ({ ...x, is_read: true })) ?? cur)
		setUnread(0)
		try {
			await markAllNotificationsRead()
		} catch {
			refreshCount()
		}
	}

	const badge = unread > 99 ? "99+" : String(unread)

	return (
		<div ref={rootRef} className="relative">
			<button
				onClick={toggle}
				aria-label={`Notifications${unread ? ` (${unread} unread)` : ""}`}
				aria-haspopup="true"
				aria-expanded={open}
				className="relative grid h-9 w-9 place-items-center rounded-full border border-slate-300 dark:border-white/15 bg-white dark:bg-slate-900 text-slate-600 dark:text-slate-300 transition hover:bg-slate-100 dark:hover:bg-white/10"
			>
				<span className="text-base" aria-hidden>
					{"\u{1F514}"}
				</span>
				{unread > 0 ? (
					<span className="absolute -right-1 -top-1 grid min-w-[1.15rem] place-items-center rounded-full bg-red-500 px-1 text-[10px] font-semibold leading-4 text-white">
						{badge}
					</span>
				) : null}
			</button>

			{open ? (
				<div className="absolute right-0 z-50 mt-2 w-80 overflow-hidden rounded-xl border border-slate-200 dark:border-white/10 bg-white dark:bg-slate-900 shadow-lg">
					<div className="flex items-center justify-between border-b border-slate-200 dark:border-white/10 px-4 py-3">
						<span className="text-sm font-semibold">Notifications</span>
						<button
							onClick={markAll}
							disabled={unread === 0}
							className="text-xs font-medium text-indigo-600 dark:text-indigo-300 disabled:opacity-40 hover:underline"
						>
							Mark all read
						</button>
					</div>
					<div className="max-h-96 overflow-y-auto">
						{loading ? (
							<div className="p-4">
								<LoadingState message={"Loading notifications\u2026"} />
							</div>
						) : error ? (
							<div className="p-4">
								<ErrorState message={error} onRetry={loadFeed} />
							</div>
						) : !items || items.length === 0 ? (
							<div className="p-4">
								<EmptyState
									title="You're all caught up"
								description="No notifications yet."
							/>
							</div>
						) : (
							<ul className="divide-y divide-slate-100 dark:divide-white/5">
								{items.map((n) => (
									<li key={n.id}>
										<button
											onClick={() => openItem(n)}
											className={`flex w-full gap-3 px-4 py-3 text-left transition hover:bg-slate-50 dark:hover:bg-white/5 ${
												n.is_read ? "" : "bg-indigo-50/60 dark:bg-indigo-500/10"
											}`}
										>
											<span className="mt-0.5 text-base" aria-hidden>
												{iconForType(n.type)}
											</span>
											<span className="min-w-0 flex-1">
												<span className="flex items-center gap-2">
													<span
														className={`truncate text-sm ${
														n.is_read
															? "text-slate-600 dark:text-slate-300"
															: "font-semibold text-slate-900 dark:text-white"
													}`}
													>
														{n.title}
													</span>
													{!n.is_read ? (
														<span
															className="h-2 w-2 shrink-0 rounded-full bg-indigo-500"
															aria-hidden
													/>
													) : null}
												</span>
												{n.body ? (
													<span className="mt-0.5 block truncate text-xs text-slate-500 dark:text-slate-400">
														{n.body}
													</span>
												) : null}
												<span className="mt-0.5 block text-[11px] text-slate-400 dark:text-slate-500">
													{timeAgo(n.created_at)}
												</span>
											</span>
										</button>
									</li>
								))}
							</ul>
						)}
					</div>
				</div>
			) : null}
		</div>
	)
}
