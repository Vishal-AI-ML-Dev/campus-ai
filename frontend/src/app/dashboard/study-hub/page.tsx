"use client"

// Study Hub route (/dashboard/study-hub). Uses the signed-in user and shows the
// teacher/admin manage panel or the student browse panel based on role.

import { useCurrentUser } from "@/lib/auth"
import MaterialTeacherPanel from "@/components/studyhub/MaterialTeacherPanel"
import MaterialStudentPanel from "@/components/studyhub/MaterialStudentPanel"

export default function StudyHubPage() {
	// The dashboard layout has already loaded the signed-in user (/auth/me);
	// reuse it instead of fetching it again (no extra "Loading..." flash).
	const { user: me } = useCurrentUser()
	if (!me) return null

	const isStaff = me.role === "teacher" || me.role === "admin"

	return (
		<div className="mx-auto max-w-3xl space-y-6 p-6">
			<div>
				<h1 className="text-xl font-semibold text-gray-900 dark:text-slate-100">📚 Study Hub</h1>
				<p className="text-sm text-gray-500 dark:text-slate-400">
					{isStaff
						? "Upload and manage study materials for your sections."
						: "Browse study materials shared with your section."}
				</p>
			</div>
			{isStaff ? <MaterialTeacherPanel /> : <MaterialStudentPanel />}
		</div>
	)
}
