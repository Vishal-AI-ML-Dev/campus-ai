"use client"

/**
 * Placement Hub shared UI: the gate frame every hub page renders inside,
 * status pills, the staff sub-navigation and small form primitives.
 */

import Link from "next/link"
import { usePathname } from "next/navigation"
import type { ReactNode } from "react"
import { useCurrentUser, type Role } from "@/lib/auth"
import { ErrorState, LoadingState } from "@/components/ui/states"
import { usePlacementGate } from "./placementApi"
import {
	LIFECYCLE_LABEL,
	LIFECYCLE_TONE,
	STATUS_LABEL,
	STATUS_TONE,
	type AppStatus,
	type Lifecycle,
} from "./placementLogic"

function Notice({ title, text }: { title: string; text: string }) {
	return (
		<div>
			<h2 className="text-2xl font-bold">{title}</h2>
			<p className="mt-2 text-slate-600 dark:text-slate-400">{text}</p>
		</div>
	)
}

/**
 * Renders children only when the hub is enabled for this institute AND the
 * signed-in role is one of `roles`. The server enforces the same rules.
 */
export function HubFrame({
	title,
	roles,
	children,
}: {
	title: string
	roles: Role[]
	children: (role: Role) => ReactNode
}) {
	const { user } = useCurrentUser()
	const gate = usePlacementGate(user?.role)
	if (gate.state === "off") return <Notice title={title} text="The Placement Hub is not available in this build." />
	if (gate.state === "loading") return <LoadingState message="Loading the Placement Hub…" />
	if (gate.state === "disabled")
		return <Notice title={title} text="The Placement Hub is not enabled for your institute yet." />
	if (gate.state === "error") return <ErrorState message={gate.message} onRetry={gate.retry} requestId={gate.requestId} />
	if (gate.state === "role" || !roles.includes(gate.role))
		return <Notice title={title} text="This page is not available for your role." />
	return <>{children(gate.role)}</>
}

export function StatusPill({ status }: { status: AppStatus }) {
	return (
		<span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_TONE[status]}`}>
			{STATUS_LABEL[status]}
		</span>
	)
}

export function LifecyclePill({ lifecycle }: { lifecycle: Lifecycle }) {
	return (
		<span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${LIFECYCLE_TONE[lifecycle]}`}>
			{LIFECYCLE_LABEL[lifecycle]}
		</span>
	)
}

export function YesNo({ ok, yes = "Eligible", no = "Not eligible" }: { ok: boolean; yes?: string; no?: string }) {
	return (
		<span
			className={`inline-flex items-center gap-1 text-xs font-medium ${
				ok ? "text-emerald-700 dark:text-emerald-300" : "text-red-700 dark:text-red-300"
			}`}
		>
			<span aria-hidden="true">{ok ? "✓" : "✗"}</span>
			{ok ? yes : no}
		</span>
	)
}

const STAFF_LINKS: { href: string; label: string; roles: Role[] }[] = [
	{ href: "/dashboard/placement-hub", label: "Overview", roles: ["tpo", "admin"] },
	{ href: "/dashboard/placement-hub/drives", label: "Drives", roles: ["tpo", "admin"] },
	{ href: "/dashboard/placement-hub/students", label: "Students", roles: ["tpo", "admin"] },
	{ href: "/dashboard/placement-hub/import", label: "Import marks", roles: ["tpo", "admin"] },
	{ href: "/dashboard/placement-hub/campaigns", label: "Campaigns", roles: ["tpo", "admin", "teacher"] },
	{ href: "/dashboard/placement-hub/analytics", label: "Analytics", roles: ["tpo", "admin"] },
]

export function StaffNav({ role }: { role: Role }) {
	const pathname = usePathname()
	const links = STAFF_LINKS.filter((l) => l.roles.includes(role))
	if (links.length < 2) return null
	return (
		<nav aria-label="Placement Hub sections" className="mb-6 overflow-x-auto relative [contain:inline-size]">
			<ul className="flex min-w-max gap-1 border-b border-slate-200 dark:border-white/10">
				{links.map((l) => {
					const active =
						l.href === "/dashboard/placement-hub" ? pathname === l.href : pathname.startsWith(l.href)
					return (
						<li key={l.href}>
							<Link
								href={l.href}
								aria-current={active ? "page" : undefined}
								className={`block rounded-t-lg px-3 py-2 text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 ${
									active
										? "border-b-2 border-indigo-500 font-semibold text-indigo-700 dark:text-indigo-300"
										: "text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-white"
								}`}
							>
								{l.label}
							</Link>
						</li>
					)
				})}
			</ul>
		</nav>
	)
}

export const inputClass =
	"mt-1 w-full min-w-0 max-w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm outline-none focus:border-indigo-500 focus-visible:ring-2 focus-visible:ring-indigo-500/40 dark:border-white/15 dark:bg-slate-900"

export const buttonClass =
	"rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 dark:focus-visible:ring-offset-slate-950"

export const secondaryButtonClass =
	"rounded-lg border border-slate-300 px-3 py-1.5 text-sm transition hover:bg-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 disabled:cursor-not-allowed disabled:opacity-50 dark:border-white/15 dark:hover:bg-white/10"

export const dangerButtonClass =
	"rounded-lg border border-red-300 px-3 py-1.5 text-sm text-red-700 transition hover:bg-red-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-500 disabled:cursor-not-allowed disabled:opacity-50 dark:border-red-500/40 dark:text-red-300 dark:hover:bg-red-500/10"

export const cardClass = "rounded-2xl border border-slate-200 bg-white p-5 dark:border-white/10 dark:bg-slate-900"

export function Field({
	id,
	label,
	hint,
	children,
}: {
	id: string
	label: string
	hint?: string
	children: ReactNode
}) {
	return (
		<div className="min-w-0">
			<label htmlFor={id} className="text-sm text-slate-700 dark:text-slate-300">
				{label}
			</label>
			{children}
			{hint ? (
				<p id={`${id}-hint`} className="mt-1 text-xs text-slate-500 dark:text-slate-400">
					{hint}
				</p>
			) : null}
		</div>
	)
}

/** Inline success / error message announced to screen readers. */
export function Flash({ kind, text }: { kind: "ok" | "error"; text: string }) {
	return (
		<p
			role={kind === "error" ? "alert" : "status"}
			className={`rounded-lg border px-3 py-2 text-sm ${
				kind === "error"
					? "border-red-300 bg-red-50 text-red-800 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-200"
					: "border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-200"
			}`}
		>
			{text}
		</p>
	)
}

/** Previous / next pager for offset pagination. */
export function Pager({
	offset,
	limit,
	total,
	onChange,
}: {
	offset: number
	limit: number
	total: number
	onChange: (offset: number) => void
}) {
	if (total <= limit) return null
	const page = Math.floor(offset / limit) + 1
	const pages = Math.max(1, Math.ceil(total / limit))
	return (
		<div className="mt-4 flex items-center justify-between gap-2 text-sm">
			<button
				type="button"
				className={secondaryButtonClass}
				disabled={offset === 0}
				onClick={() => onChange(Math.max(0, offset - limit))}
			>
				Previous
			</button>
			<span aria-live="polite">
				Page {page} of {pages} ({total} total)
			</span>
			<button
				type="button"
				className={secondaryButtonClass}
				disabled={offset + limit >= total}
				onClick={() => onChange(offset + limit)}
			>
				Next
			</button>
		</div>
	)
}
