"use client"

/**
 * Phase 8.4: the transparency note for a company-context session (shown at
 * setup, before start and with the report). Plain text only: sources show a
 * label and domain, never a link; institute text is attributed "From your
 * institute"; the disclaimer and the evaluation note are always present.
 */

import type { CompanyContext } from "./companyApi"
import { EVALUATION_NOTE, sourceText } from "./companyFlow"

export default function CompanyContextNotice({ context }: { context: CompanyContext }) {
	return (
		<aside
			aria-label="About the company focus"
			className="space-y-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-200"
		>
			<p className="font-medium">
				{context.name ? `${context.name} · ` : ""}
				{context.role_title}
				{context.role_template_label ? ` · ${context.role_template_label}` : ""}
			</p>
			<p>{context.disclaimer}</p>
			<p>{EVALUATION_NOTE}</p>
			{context.sources.length ? (
				<div>
					<p>
						{context.attribution}. Sources:
					</p>
					<ul className="list-disc pl-5">
						{context.sources.map((s, i) => (
							<li key={i}>{sourceText(s)}</li>
						))}
					</ul>
				</div>
			) : null}
		</aside>
	)
}
