/**
 * Campus AI - authenticated app shell (sidebar + topbar layout).
 *
 * Place this file at: src/components/AppShell.tsx
 *
 * Renders a role-aware sidebar (links from NAV_BY_ROLE), a topbar with the
 * signed-in user + logout, and the page content. Must be used inside
 * <AuthProvider> (it reads the user from useCurrentUser).
 *
 * Phase 9 closure (L8): from 768 px (`md`) the sidebar is the same static
 * column as before. Below 768 px it is an off-canvas drawer, hidden (and not
 * focusable) until the header menu button opens it; Escape, the backdrop,
 * the close button or a navigation closes it and focus returns to the menu
 * button. While open, focus stays inside it and the page behind is inert.
 */

"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import Link from "next/link"
import { usePathname, useRouter } from "next/navigation"
import { api, clearToken } from "@/lib/api"
import { useCurrentUser, NAV_BY_ROLE } from "@/lib/auth"
import ThemeToggle from "@/components/ThemeToggle"
import NotificationBell from "@/components/notifications/NotificationBell"
import { useMockInterviewNavItem } from "@/components/mock-interview/mockInterviewFlags"
import { useInterviewOpsNavItem } from "@/components/interview-operations/opsApi"
import { useCompanyProfilesNavItem } from "@/components/interview-company-profiles/companyProfilesApi"
import { usePlacementHubNavItem } from "@/components/placement-hub/placementApi"

export default function AppShell({ children }: { children: React.ReactNode }) {
	const router = useRouter()
	const pathname = usePathname()
	const { user, loading } = useCurrentUser()
	// AI Mock Interview nav entry: null (and no request) unless its build flag is on.
	const mockInterviewNavItem = useMockInterviewNavItem(user?.role)
	// Phase 7.8: Interview Ops for admins/TPOs; null (no request) unless its build flag is on.
	const interviewOpsNavItem = useInterviewOpsNavItem(user?.role)
	// Phase 8.4: Company Profiles for admins/TPOs; only when the server has them on.
	const companyProfilesNavItem = useCompanyProfilesNavItem(user?.role)
	// Phase 9.2: Placement Hub; null (no request) unless its build flag is on.
	const placementHubNavItem = usePlacementHubNavItem(user?.role)

	// L8: mobile navigation drawer (only reachable below the `md` breakpoint).
	const [navOpen, setNavOpen] = useState(false)
	const menuButtonRef = useRef<HTMLButtonElement>(null)
	const drawerRef = useRef<HTMLElement>(null)
	const closeButtonRef = useRef<HTMLButtonElement>(null)
	// Focus is restored AFTER the close commits: until then the page behind the
	// drawer is still `inert`, and focusing the menu button would be ignored.
	const restoreFocusRef = useRef(false)
	const closeNav = useCallback((restoreFocus: boolean) => {
		restoreFocusRef.current = restoreFocus
		setNavOpen(false)
	}, [])
	useEffect(() => {
		if (!navOpen && restoreFocusRef.current) {
			restoreFocusRef.current = false
			menuButtonRef.current?.focus()
		}
	}, [navOpen])

	// Navigating (pathname change) closes the drawer.
	const [navPath, setNavPath] = useState(pathname)
	if (navPath !== pathname) {
		setNavPath(pathname)
		setNavOpen(false)
	}

	useEffect(() => {
		if (!navOpen) return
		closeButtonRef.current?.focus()
		const desktop = window.matchMedia("(min-width: 768px)")
		const onResize = () => {
			if (desktop.matches) setNavOpen(false)
		}
		const onKey = (event: KeyboardEvent) => {
			if (event.key === "Escape") {
				event.preventDefault()
				closeNav(true)
				return
			}
			if (event.key !== "Tab" || !drawerRef.current) return
			const items = drawerRef.current.querySelectorAll<HTMLElement>("a[href], button:not([disabled])")
			if (items.length === 0) return
			const first = items[0]
			const last = items[items.length - 1]
			if (event.shiftKey && document.activeElement === first) {
				event.preventDefault()
				last.focus()
			} else if (!event.shiftKey && document.activeElement === last) {
				event.preventDefault()
				first.focus()
			}
		}
		desktop.addEventListener("change", onResize)
		document.addEventListener("keydown", onKey)
		return () => {
			desktop.removeEventListener("change", onResize)
			document.removeEventListener("keydown", onKey)
		}
	}, [navOpen, closeNav])

	// While the provider is loading (or redirecting), show a simple splash.
	if (loading || !user) {
		return (
			<div className="flex min-h-screen items-center justify-center bg-slate-50 dark:bg-slate-950 text-slate-500 dark:text-slate-400">
				<p>Loading...</p>
			</div>
		)
	}

	const baseNav = NAV_BY_ROLE[user.role] ?? []
	const nav = [...baseNav, placementHubNavItem, mockInterviewNavItem, interviewOpsNavItem, companyProfilesNavItem].filter(
		(item): item is NonNullable<typeof item> => item !== null,
	)

	function isActive(href: string): boolean {
		// "Overview" (/dashboard) should only match exactly; others match prefix.
		return href === "/dashboard"
			? pathname === "/dashboard"
			: pathname.startsWith(href)
	}

	async function handleLogout() {
		// Revoke the refresh-token family + clear the cookies on the backend, then
		// purge any legacy localStorage token and bounce to login.
		try {
			await api.logout()
		} catch {
			// Ignore network/expiry errors; we log out locally regardless.
		}
		clearToken()
		router.replace("/login")
	}

	return (
		<div className="flex min-h-screen bg-slate-50 dark:bg-slate-950 text-slate-900 dark:text-slate-100">
			{/* Mobile backdrop (below md only, while the drawer is open) */}
			{navOpen && (
				<div
					aria-hidden="true"
					data-testid="nav-backdrop"
					onClick={() => closeNav(true)}
					className="fixed inset-0 z-30 bg-slate-900/50 md:hidden"
				/>
			)}

			{/* Sidebar: static column from md; off-canvas drawer below md */}
			<aside
				id="app-sidebar"
				ref={drawerRef}
				aria-label="Main navigation"
				className={`${
					navOpen ? "fixed inset-y-0 left-0 z-40 flex shadow-xl" : "hidden"
				} w-60 shrink-0 flex-col overflow-y-auto border-r border-slate-200 dark:border-white/10 bg-white dark:bg-slate-900 md:static md:z-auto md:flex md:overflow-visible md:shadow-none`}
			>
				<div className="flex items-center gap-2 px-5 py-5 font-semibold">
					<span className="grid h-8 w-8 place-items-center rounded-lg bg-gradient-to-br from-indigo-500 to-violet-500 text-sm font-bold">
						C
					</span>
					<span>Campus AI</span>
					<button
						ref={closeButtonRef}
						type="button"
						onClick={() => closeNav(true)}
						aria-label="Close navigation"
						className="ml-auto rounded-lg px-2 py-1 text-lg leading-none text-slate-500 dark:text-slate-400 transition hover:bg-slate-100 dark:hover:bg-white/10 md:hidden"
					>
						<span aria-hidden="true">×</span>
					</button>
				</div>

				<nav className="flex-1 space-y-1 px-3 py-2">
					{nav.map((item) => (
						<Link
							key={item.href}
							href={item.href}
							onClick={() => setNavOpen(false)}
							aria-current={isActive(item.href) ? "page" : undefined}
							className={`flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition ${
								isActive(item.href)
									? "bg-indigo-50 dark:bg-indigo-500/15 text-indigo-700 dark:text-indigo-200"
								: "text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-white/10 hover:text-slate-900 dark:hover:text-white"
							}`}
						>
							<span className="text-base">{item.icon}</span>
							{item.label}
						</Link>
					))}
				</nav>

				<div className="border-t border-slate-200 dark:border-white/10 p-3">
					<div className="truncate px-2 text-sm font-medium">{user.full_name}</div>
					<div className="truncate px-2 text-xs text-slate-500 dark:text-slate-400">{user.email}</div>
					<button
						onClick={handleLogout}
						className="mt-3 w-full rounded-lg border border-slate-300 dark:border-white/15 px-3 py-2 text-sm transition hover:bg-slate-100 dark:hover:bg-white/10"
					>
						Logout
					</button>
				</div>
			</aside>

			{/* Main column (inert behind the open mobile drawer) */}
			<div className="flex min-w-0 flex-1 flex-col" inert={navOpen}>
				<header className="flex items-center justify-between gap-2 border-b border-slate-200 dark:border-white/10 px-4 py-4 md:px-8">
					<div className="flex min-w-0 items-center gap-2">
						<button
							ref={menuButtonRef}
							type="button"
							onClick={() => setNavOpen(true)}
							aria-label="Open navigation"
							aria-expanded={navOpen}
							aria-controls="app-sidebar"
							className="shrink-0 rounded-lg border border-slate-300 dark:border-white/15 px-2.5 py-1.5 text-base leading-none transition hover:bg-slate-100 dark:hover:bg-white/10 md:hidden"
						>
							<span aria-hidden="true">☰</span>
						</button>
						<h1 className="truncate text-lg font-semibold capitalize">{user.role} dashboard</h1>
					</div>
					<div className="flex shrink-0 items-center gap-2 sm:gap-3">
						<NotificationBell />
						<ThemeToggle />
						<span className="hidden rounded-full border border-slate-300 dark:border-white/15 bg-white dark:bg-slate-900 px-3 py-1 text-xs capitalize text-slate-600 dark:text-slate-300 sm:inline">
							{user.role}
						</span>
					</div>
				</header>
				<main className="min-w-0 flex-1 px-4 py-6 md:px-8 md:py-8">{children}</main>
			</div>
		</div>
	)
}
