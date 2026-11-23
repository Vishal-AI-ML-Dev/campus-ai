"use client"

/**
 * Phase 8.2: one problem's statement, formats, constraints, examples, visible
 * tests and the hints revealed so far. Everything is rendered as plain text
 * (no HTML, no Markdown rendering): the bank allows no HTML, links or images.
 */

import type { CodingProblem } from "./codingApi"

const DIFFICULTY: Record<string, string> = { easy: "Beginner", medium: "Intermediate", hard: "Advanced" }

function Block({ title, children }: { title: string; children: React.ReactNode }) {
	return (
		<section className="space-y-1">
			<h4 className="text-sm font-semibold text-slate-800 dark:text-slate-100">{title}</h4>
			{children}
		</section>
	)
}

function Pre({ text, label }: { text: string; label: string }) {
	return (
		<pre
			aria-label={label}
			className="overflow-x-auto whitespace-pre rounded-md bg-slate-100 p-2 font-mono text-xs text-slate-800 dark:bg-slate-800 dark:text-slate-100"
		>
			{text}
		</pre>
	)
}

export default function ProblemPanel({ problem, headingId }: { problem: CodingProblem; headingId: string }) {
	return (
		<article aria-labelledby={headingId} className="space-y-4 text-sm text-slate-700 dark:text-slate-200">
			<header>
				<h3 id={headingId} className="text-base font-semibold text-slate-900 dark:text-white">
					{problem.title}
				</h3>
				<p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
					{DIFFICULTY[problem.difficulty] ?? problem.difficulty} · time limit {problem.time_limit_ms / 1000} s ·
					memory {problem.memory_limit_mb} MB
				</p>
			</header>
			<p className="whitespace-pre-wrap">{problem.statement}</p>
			<Block title="Input">
				<p className="whitespace-pre-wrap">{problem.input_format}</p>
			</Block>
			<Block title="Output">
				<p className="whitespace-pre-wrap">{problem.output_format}</p>
			</Block>
			<Block title="Constraints">
				<p className="whitespace-pre-wrap">{problem.constraints}</p>
			</Block>
			<Block title="Examples">
				<ol className="space-y-3">
					{problem.examples.map((ex, i) => (
						<li key={i} className="space-y-1">
							<p className="text-xs font-medium">Example {i + 1}</p>
							<Pre label={`Example ${i + 1} input`} text={ex.input} />
							<Pre label={`Example ${i + 1} output`} text={ex.output} />
							{ex.explanation ? <p className="whitespace-pre-wrap text-xs">{ex.explanation}</p> : null}
						</li>
					))}
				</ol>
			</Block>
			{problem.visible_tests.length ? (
				<details className="rounded-md border border-slate-200 p-2 dark:border-white/10">
					<summary className="cursor-pointer text-sm font-medium">
						Sample tests ({problem.visible_tests.length})
					</summary>
					<ol className="mt-2 space-y-3">
						{problem.visible_tests.map((t, i) => (
							<li key={i} className="space-y-1">
								<p className="text-xs font-medium">Sample {i + 1}</p>
								<Pre label={`Sample ${i + 1} input`} text={t.input || "(empty input)"} />
								<Pre label={`Sample ${i + 1} expected output`} text={t.output} />
							</li>
						))}
					</ol>
				</details>
			) : null}
			{problem.hints_revealed.length ? (
				<Block title="Hints you opened">
					<ol className="list-decimal space-y-1 pl-5">
						{problem.hints_revealed.map((h, i) => (
							<li key={i} className="whitespace-pre-wrap">
								{h}
							</li>
						))}
					</ol>
				</Block>
			) : null}
		</article>
	)
}
