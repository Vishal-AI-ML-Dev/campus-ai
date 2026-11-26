/**
 * Campus AI - Company Profiles for practice interviews (AI Mock Interview, Phase 8.4).
 *
 * Institute admins and TPOs create, edit, publish and archive company profiles
 * (decision P8-D9); "Create from drive" pre-fills a draft from an open placement
 * drive of the institute. Publishing needs at least one source record (P8-D10).
 * Profiles are never deleted. Students only see published profiles, labelled
 * "From your institute"; profiles change question generation only (P8-D11).
 *
 * Shown only in builds with NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED; the backend
 * decides per institute (AI_MOCK_INTERVIEW_COMPANY_ENABLED, 404 = not enabled)
 * and per role (403). Deliberately NOT under /dashboard/mock-interview, so the
 * camera permission policy of that route never applies here.
 */

"use client"

import { useState } from "react"
import { useCurrentUser } from "@/lib/auth"
import { useApiResource } from "@/lib/useApiResource"
import { EmptyState, ErrorState, LoadingState, Spinner } from "@/components/ui/states"
import { describeError } from "@/components/mock-interview/mockInterviewApi"
import type { RoleTemplate } from "@/components/mock-interview/company/companyApi"
import { sourceText } from "@/components/mock-interview/company/companyFlow"
import {
	COMPANY_PROFILES_BUILD_ENABLED,
	companyProfilesApi,
	type OpenDrive,
	type StaffProfile,
} from "@/components/interview-company-profiles/companyProfilesApi"
import ProfileForm from "@/components/interview-company-profiles/ProfileForm"
import { STATUS_LABELS } from "@/components/interview-company-profiles/profileDraft"

function Notice({ text }: { text: string }) {
	return (
		<div>
			<h1 className="text-2xl font-bold">Company Profiles</h1>
			<p className="mt-2 text-slate-500 dark:text-slate-400">{text}</p>
		</div>
	)
}

export default function InterviewCompanyProfilesPage() {
	const { user } = useCurrentUser()
	const staff = user?.role === "admin" || user?.role === "tpo"
	const allowed = COMPANY_PROFILES_BUILD_ENABLED && staff
	const templates = useApiResource<RoleTemplate[]>(allowed ? companyProfilesApi.templatesPath : null)
	const list = useApiResource<StaffProfile[]>(allowed ? companyProfilesApi.listPath : null)
	const drives = useApiResource<OpenDrive[]>(allowed ? companyProfilesApi.openDrivesPath : null)
	const [editing, setEditing] = useState<StaffProfile | "new" | null>(null)
	const [busy, setBusy] = useState<string | null>(null)
	const [message, setMessage] = useState<string | null>(null)
	const [fromDrive, setFromDrive] = useState("")

	if (!COMPANY_PROFILES_BUILD_ENABLED) return <Notice text="Company profiles are not available in this build." />
	if (!user) return <LoadingState />
	if (!staff) return <Notice text="Company profiles are managed by institute admins and TPOs only." />
	if (templates.loading || list.loading) return <LoadingState message="Loading company profiles…" />
	if (templates.error || list.error || !templates.data || !list.data)
		return <Notice text="Company profiles are not enabled for your institute." />

	const openDrives = drives.data ?? []

	async function act(key: string, run: () => Promise<StaffProfile>, done: string) {
		if (busy) return
		setBusy(key)
		setMessage(null)
		try {
			await run()
			setMessage(done)
			list.reload()
		} catch (err) {
			setMessage(describeError(err, "That did not work. Please try again."))
		} finally {
			setBusy(null)
		}
	}

	if (editing)
		return (
			<ProfileForm
				profile={editing === "new" ? null : editing}
				templates={templates.data}
				drives={openDrives}
				onCancel={() => setEditing(null)}
				onSaved={() => {
					setEditing(null)
					setMessage("Profile saved.")
					list.reload()
				}}
			/>
		)

	return (
		<div className="space-y-6">
			<div>
				<h1 className="text-2xl font-bold">Company Profiles</h1>
				<p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
					Profiles help students practise interviews for a company and role. Students see them as information from
					your institute, with your sources. They only change which skills practice questions focus on; scores use
					the standard rubric, and questions are never presented as real company questions.
				</p>
			</div>

			<div className="flex flex-wrap items-end gap-3">
				<button
					type="button"
					onClick={() => setEditing("new")}
					className="min-h-[44px] rounded-lg bg-gradient-to-r from-indigo-500 to-violet-500 px-4 py-2 text-sm font-medium text-white"
				>
					New profile
				</button>
				<label className="text-sm">
					<span className="block font-medium">Create from an open drive</span>
					<select
						value={fromDrive}
						onChange={(e) => setFromDrive(e.target.value)}
						className="mt-1 min-h-[44px] rounded-lg border border-slate-200 bg-white px-2 text-sm dark:border-white/10 dark:bg-slate-900"
					>
						<option value="">Choose a drive</option>
						{openDrives.map((d) => (
							<option key={d.id} value={d.id}>
								#{d.id} {d.company_name} - {d.role_title}
							</option>
						))}
					</select>
				</label>
				<button
					type="button"
					disabled={!fromDrive || busy !== null}
					onClick={() =>
						void act("drive", () => companyProfilesApi.fromDrive(Number(fromDrive)), "Draft created from the drive.")
					}
					className="inline-flex min-h-[44px] items-center gap-2 rounded-lg border border-slate-300 px-4 text-sm disabled:opacity-50 dark:border-white/20"
				>
					{busy === "drive" ? <Spinner /> : null}
					Create draft
				</button>
			</div>

			{message ? (
				<p role="status" className="rounded-lg border border-slate-200 px-3 py-2 text-sm dark:border-white/10">
					{message}
				</p>
			) : null}

			{list.data.length === 0 ? (
				<EmptyState title="No company profiles yet" description="Create one, or start from an open placement drive." />
			) : (
				<ul className="space-y-3">
					{list.data.map((p) => (
						<li key={p.id} className="space-y-2 rounded-xl border border-slate-200 bg-white p-4 dark:border-white/10 dark:bg-slate-900">
							<div className="flex flex-wrap items-center gap-2">
								<h2 className="text-base font-semibold">
									{p.name} - {p.role_title}
								</h2>
								<span className="rounded-full border border-slate-300 px-2 text-xs dark:border-white/20">
									{STATUS_LABELS[p.status] ?? p.status}
								</span>
							</div>
							{p.sources.length ? (
								<ul className="list-disc pl-5 text-xs text-slate-600 dark:text-slate-300">
									{p.sources.map((s, i) => (
										<li key={i}>{sourceText(s)}</li>
									))}
								</ul>
							) : null}
							{p.publish_blockers.length ? (
								<p className="text-xs text-amber-700 dark:text-amber-300">{p.publish_blockers.join(" ")}</p>
							) : null}
							<div className="flex flex-wrap gap-2">
								{p.status !== "archived" ? (
									<button
										type="button"
										onClick={() => setEditing(p)}
										className="min-h-[44px] rounded-lg border border-slate-300 px-3 text-sm dark:border-white/20"
									>
										Edit
									</button>
								) : null}
								{p.status === "draft" ? (
									<button
										type="button"
										disabled={!p.can_publish || busy !== null}
										onClick={() => void act(`publish-${p.id}`, () => companyProfilesApi.publish(p.id), "Profile published.")}
										className="inline-flex min-h-[44px] items-center gap-2 rounded-lg border border-slate-300 px-3 text-sm disabled:opacity-50 dark:border-white/20"
									>
										{busy === `publish-${p.id}` ? <Spinner /> : null}
										Publish
									</button>
								) : null}
								{p.status !== "archived" ? (
									<button
										type="button"
										disabled={busy !== null}
										onClick={() => void act(`archive-${p.id}`, () => companyProfilesApi.archive(p.id), "Profile archived.")}
										className="inline-flex min-h-[44px] items-center gap-2 rounded-lg border border-slate-300 px-3 text-sm disabled:opacity-50 dark:border-white/20"
									>
										{busy === `archive-${p.id}` ? <Spinner /> : null}
										Archive
									</button>
								) : null}
							</div>
						</li>
					))}
				</ul>
			)}
			{drives.error ? (
				<ErrorState message="Could not load open drives." onRetry={drives.reload} requestId={drives.requestId} />
			) : null}
		</div>
	)
}
