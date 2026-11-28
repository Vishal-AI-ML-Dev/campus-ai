"use client"

// Doubt Forum route (/dashboard/doubts). Uses the signed-in user and shows the
// teacher/admin moderation panel or the student ask/browse panel by role.

import { useCurrentUser } from "@/lib/auth"
import DoubtStudentPanel from "@/components/doubts/DoubtStudentPanel"
import DoubtTeacherPanel from "@/components/doubts/DoubtTeacherPanel"

export default function DoubtsPage() {
	// The dashboard layout has already loaded the signed-in user (/auth/me);
	// reuse it instead of fetching it again (no extra "Loading..." flash).
	const { user: me } = useCurrentUser()
	if (!me) return null

	const isStaff = me.role === "teacher" || me.role === "admin"

	return (
		<div className="mx-auto max-w-3xl space-y-6 p-6">
			<div>
				<h1 className="text-xl font-semibold text-gray-900 dark:text-slate-100">
					💬 Doubt Forum
				</h1>
				<p className="text-sm text-gray-500 dark:text-slate-400">
					{isStaff
						? "Browse and answer your sections' doubts, and accept the best answer."
						: "Ask doubts and get answers from your peers and teachers."}
				</p>
			</div>
			{isStaff ? (
				<DoubtTeacherPanel me={me} />
			) : (
				<DoubtStudentPanel me={me} />
			)}
		</div>
	)
}
