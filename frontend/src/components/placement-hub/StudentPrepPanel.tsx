"use client"

/** Discoverable entry to interview preparation from the student hub (Phase 9.4). */

import Link from "next/link"
import { cardClass } from "./HubUi"

export default function StudentPrepPanel() {
	return (
		<section aria-labelledby="sp-h" className={`${cardClass} mt-6`}>
			<h3 id="sp-h" className="text-lg font-semibold">
				Interview preparation
			</h3>
			<p className="mt-1 text-sm text-slate-600 dark:text-slate-300">
				Open any drive above and choose <strong>Practise now</strong> to rehearse with the AI mock interview using that
				drive&apos;s role and company. Your practice results are private to you; faculty only see whether assigned
				practice was completed.
			</p>
			<Link href="/dashboard/mock-interview" className="mt-2 inline-block text-sm text-indigo-700 hover:underline dark:text-indigo-300">
				Open AI Mock Interview {"→"}
			</Link>
		</section>
	)
}
