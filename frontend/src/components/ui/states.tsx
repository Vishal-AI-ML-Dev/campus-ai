"use client"

// Shared UI primitives for the three async states every data view needs:
// loading, error, and empty. Centralising these keeps look-and-feel consistent
// across the whole dashboard and removes the copy-pasted one-liners that used to
// live in every panel.

import type { ReactNode } from "react"

/** Small inline spinner that inherits the current text color. */
export function Spinner({ className = "h-4 w-4" }: { className?: string }) {
	return (
		<svg
			className={`animate-spin ${className}`}
			viewBox="0 0 24 24"
			fill="none"
			role="status"
			aria-label="Loading"
		>
			<circle
				className="opacity-25"
				cx="12"
				cy="12"
				r="10"
				stroke="currentColor"
				strokeWidth="4"
			/>
			<path
				className="opacity-75"
				fill="currentColor"
				d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"
			/>
		</svg>
	)
}

/** Standard inline loading row: spinner + message. */
export function LoadingState({ message = "Loading\u2026" }: { message?: string }) {
	return (
		<div
			className="flex items-center gap-2 text-sm text-gray-500 dark:text-slate-400"
			role="status"
		>
			<Spinner />
			<span>{message}</span>
		</div>
	)
}

/**
 * Standard error box with an optional "Try again" button and a backend request
 * id (from ApiError) that the user can quote in a bug report.
 */
export function ErrorState({
	message,
	onRetry,
	requestId,
}: {
	message: string
	onRetry?: () => void
	requestId?: string
}) {
	return (
		<div
			role="alert"
			className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm dark:border-red-500/30 dark:bg-red-500/10"
		>
			<p className="font-medium text-red-700 dark:text-red-300">{message}</p>
			{requestId ? (
				<p className="mt-1 text-xs text-red-500 dark:text-red-400">
					Reference: <code>{requestId}</code>
				</p>
			) : null}
			{onRetry ? (
				<button
					onClick={onRetry}
					className="mt-3 rounded-md border border-red-300 px-3 py-1 text-xs font-medium text-red-700 hover:bg-red-100 dark:border-red-500/40 dark:text-red-300 dark:hover:bg-red-500/20"
				>
					Try again
				</button>
			) : null}
		</div>
	)
}

/** Standard empty placeholder with optional icon, description, and action. */
export function EmptyState({
	title,
	description,
	icon,
	action,
}: {
	title: string
	description?: string
	icon?: ReactNode
	action?: ReactNode
}) {
	return (
		<div className="rounded-lg border border-dashed border-gray-300 p-8 text-center dark:border-white/10">
			{icon ? (
				<div className="mb-2 flex justify-center text-gray-400 dark:text-slate-500">
					{icon}
				</div>
			) : null}
			<p className="text-sm font-medium text-gray-700 dark:text-slate-300">
				{title}
			</p>
			{description ? (
				<p className="mt-1 text-sm text-gray-500 dark:text-slate-400">
					{description}
				</p>
			) : null}
			{action ? <div className="mt-4 flex justify-center">{action}</div> : null}
		</div>
	)
}

/** Single pulsing skeleton bar. */
export function Skeleton({ className = "h-4 w-full" }: { className?: string }) {
	return (
		<div
			className={`animate-pulse rounded bg-gray-200 dark:bg-white/10 ${className}`}
		/>
	)
}

/** A list of card-shaped skeletons used as a list-loading placeholder. */
export function SkeletonList({ rows = 3 }: { rows?: number }) {
	return (
		<div className="space-y-3" role="status" aria-label="Loading">
			{[...Array(rows).keys()].map((i) => (
				<div
					key={i}
					className="rounded-lg border border-gray-200 p-4 dark:border-white/10"
				>
					<Skeleton className="h-4 w-1/3" />
					<Skeleton className="mt-2 h-3 w-2/3" />
				</div>
			))}
		</div>
	)
}
