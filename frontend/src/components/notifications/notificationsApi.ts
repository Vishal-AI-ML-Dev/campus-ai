// In-app notifications client-side types + helpers. Mirrors the backend
// /notifications responses (feed, unread count, mark-read).

import { api } from "@/lib/api"

export type NotificationOut = {
	id: number
	type: string
	title: string
	body: string | null
	link: string | null
	actor_id: number | null
	is_read: boolean
	read_at: string | null
	created_at: string
}

export type UnreadCountOut = { unread: number }
export type MarkReadResult = { updated: number }

/** Fetch the caller's own notification feed (newest first). */
export function fetchNotifications(
	unreadOnly = false,
): Promise<NotificationOut[]> {
	const qs = unreadOnly ? "?unread_only=true" : ""
	return api.get<NotificationOut[]>(`/notifications${qs}`)
}

/** Fetch the unread count that drives the bell badge. */
export function fetchUnreadCount(): Promise<UnreadCountOut> {
	return api.get<UnreadCountOut>("/notifications/unread-count")
}

/** Mark a single notification (the caller's own) as read. */
export function markNotificationRead(id: number): Promise<NotificationOut> {
	return api.post<NotificationOut>(`/notifications/${id}/read`)
}

/** Mark ALL of the caller's unread notifications as read. */
export function markAllNotificationsRead(): Promise<MarkReadResult> {
	return api.post<MarkReadResult>("/notifications/read-all")
}

/** A small emoji icon per notification type. */
export function iconForType(type: string): string {
	switch (type) {
		case "announcement":
			return "\u{1F4E3}"
		case "application_status":
			return "\u{1F4E8}"
		case "doubt_answer":
			return "\u{1F4AC}"
		case "submission_graded":
			return "\u{1F4DD}"
		default:
			return "\u{1F514}"
	}
}

/** Best-effort relative "time ago" label for the feed (local time). */
export function timeAgo(iso: string): string {
	try {
		const then = new Date(iso).getTime()
		const secs = Math.max(0, Math.floor((Date.now() - then) / 1000))
		if (secs < 60) return "just now"
		const mins = Math.floor(secs / 60)
		if (mins < 60) return `${mins}m ago`
		const hours = Math.floor(mins / 60)
		if (hours < 24) return `${hours}h ago`
		const days = Math.floor(hours / 24)
		if (days < 7) return `${days}d ago`
		return new Date(iso).toLocaleDateString()
	} catch {
		return iso
	}
}
