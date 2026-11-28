"use client"

import { useCurrentUser } from "@/lib/auth"

import AssignmentStudentPanel from "@/components/assignments/AssignmentStudentPanel"
import AssignmentTeacherPanel from "@/components/assignments/AssignmentTeacherPanel"

// Role-aware Assignments page: staff get the create/grade panel, students get
// the submit/grade-view panel. The role comes from the signed-in user the
// dashboard layout has already loaded (/auth/me), so there is no second fetch.
export default function AssignmentsPage() {
	const { user: me } = useCurrentUser()
	const isStaff = me?.role === "teacher" || me?.role === "admin"

	return (
		<div className="mx-auto max-w-3xl space-y-6 p-6">
			<header>
				<h1 className="text-xl font-semibold text-slate-900 dark:text-slate-100">📝 Assignments</h1>
				<p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
					{isStaff
						? "Create assignments for a section and grade student submissions."
						: "View your assignments, submit your work, and see your grades."}
				</p>
			</header>

			{!me ? null : isStaff ? (
				<AssignmentTeacherPanel />
			) : (
				<AssignmentStudentPanel />
			)}
		</div>
	)
}
