"use client"

/**
 * Phase 8.2: code editor (decision P8-D5).
 *
 * CodeMirror 6 is loaded with dynamic import() only when this component is on
 * screen (the coding room), so no other page carries it. If it cannot load,
 * or the student prefers it, a plain <textarea> ("Simple editor") is used.
 *
 * Keyboard: Tab indents. To leave the editor with the keyboard press Escape,
 * then Tab (CodeMirror's tab-focus escape; the textarea does the same).
 * Ctrl/Cmd+Shift+Enter submits. The code area is always light-on-dark text
 * contrast independent of the page theme.
 *
 * Remount (change `key`) to load different code; afterwards the editor owns
 * the text and reports every change through onChange.
 */

import { useEffect, useRef, useState } from "react"
import { EDITOR_GRAMMAR } from "./codingFlow"

type Props = {
	id: string
	label: string
	language: string
	initialValue: string
	onChange: (value: string) => void
	onSubmitShortcut?: () => void
	simple: boolean
	fontSize: number
	readOnly?: boolean
	describedBy?: string
}

type Status = "loading" | "ready" | "failed"

async function loadGrammar(grammar: "python" | "cpp" | "java") {
	if (grammar === "python") return (await import("@codemirror/lang-python")).python()
	if (grammar === "java") return (await import("@codemirror/lang-java")).java()
	return (await import("@codemirror/lang-cpp")).cpp()
}

export default function CodeEditor(props: Props) {
	if (props.simple) return <SimpleEditor {...props} />
	return <RichEditor {...props} />
}

function RichEditor(props: Props) {
	const { id, label, language, initialValue, fontSize, readOnly = false, describedBy } = props
	const host = useRef<HTMLDivElement>(null)
	const onChange = useRef(props.onChange)
	const onSubmit = useRef(props.onSubmitShortcut)
	const [status, setStatus] = useState<Status>("loading")
	// Text handed to the simple editor if CodeMirror fails to load.
	const [fallbackText, setFallbackText] = useState(initialValue)
	// The latest text: a recreated editor (font size change) keeps the student's edits.
	const latest = useRef(initialValue)

	useEffect(() => {
		onChange.current = props.onChange
		onSubmit.current = props.onSubmitShortcut
	})

	useEffect(() => {
		let destroyed = false
		let view: { destroy: () => void } | null = null
		Promise.all([
			import("@codemirror/state"),
			import("@codemirror/view"),
			import("@codemirror/commands"),
			import("@codemirror/language"),
			loadGrammar(EDITOR_GRAMMAR[language] ?? "cpp"),
		])
			.then(([state, cmView, commands, lang, grammar]) => {
				if (destroyed || !host.current) return
				const extensions = [
					cmView.lineNumbers(),
					cmView.highlightActiveLine(),
					cmView.drawSelection(),
					commands.history(),
					lang.indentOnInput(),
					lang.bracketMatching(),
					lang.syntaxHighlighting(lang.defaultHighlightStyle, { fallback: true }),
					cmView.keymap.of([
						{
							key: "Mod-Shift-Enter",
							run: () => {
								onSubmit.current?.()
								return true
							},
						},
						...commands.defaultKeymap,
						...commands.historyKeymap,
						commands.indentWithTab,
					]),
					grammar,
					cmView.EditorView.updateListener.of((update) => {
						if (!update.docChanged) return
						latest.current = update.state.doc.toString()
						onChange.current(latest.current)
					}),
					cmView.EditorView.contentAttributes.of({
						"aria-label": label,
						...(describedBy ? { "aria-describedby": describedBy } : {}),
						spellcheck: "false",
						autocapitalize: "off",
						autocorrect: "off",
					}),
					cmView.EditorView.theme({
						"&": { fontSize: `${fontSize}px`, height: "100%", backgroundColor: "#ffffff", color: "#0f172a" },
						".cm-scroller": { fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace", minHeight: "18rem" },
						".cm-gutters": { backgroundColor: "#f8fafc", color: "#475569", border: "none" },
						"&.cm-focused": { outline: "2px solid #6366f1", outlineOffset: "0" },
					}),
					state.EditorState.readOnly.of(readOnly),
					state.EditorState.tabSize.of(4),
				]
				view = new cmView.EditorView({
					state: state.EditorState.create({ doc: latest.current, extensions }),
					parent: host.current,
				})
				setStatus("ready")
			})
			.catch(() => {
				if (destroyed) return
				setFallbackText(latest.current)
				setStatus("failed")
			})
		return () => {
			destroyed = true
			view?.destroy()
		}
	}, [language, label, fontSize, readOnly, describedBy])

	if (status === "failed")
		return (
			<div className="space-y-2">
				<p role="status" className="text-xs text-amber-700 dark:text-amber-300">
					The code editor could not load, so the simple editor is shown instead.
				</p>
				<SimpleEditor {...props} initialValue={fallbackText} />
			</div>
		)
	return (
		<div className="relative min-h-[18rem] overflow-hidden rounded-lg border border-slate-300 dark:border-white/20">
			{status === "loading" ? (
				<p className="absolute inset-0 grid place-items-center text-xs text-slate-500" role="status">
					Loading the code editor…
				</p>
			) : null}
			<div id={id} ref={host} className="h-full" />
		</div>
	)
}

function SimpleEditor({ id, label, initialValue, onChange, onSubmitShortcut, fontSize, readOnly, describedBy }: Props) {
	const [value, setValue] = useState(initialValue)
	const escaped = useRef(false)

	function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
		if (e.key === "Enter" && e.shiftKey && (e.ctrlKey || e.metaKey)) {
			e.preventDefault()
			onSubmitShortcut?.()
			return
		}
		if (e.key === "Escape") {
			escaped.current = true // the next Tab leaves the editor
			return
		}
		if (e.key === "Tab" && !escaped.current && !e.shiftKey && !readOnly) {
			e.preventDefault()
			const el = e.currentTarget
			const { selectionStart: start, selectionEnd: end } = el
			const next = `${value.slice(0, start)}    ${value.slice(end)}`
			setValue(next)
			onChange(next)
			requestAnimationFrame(() => {
				el.selectionStart = el.selectionEnd = start + 4
			})
			return
		}
		escaped.current = false
	}

	return (
		<textarea
			id={id}
			aria-label={label}
			aria-describedby={describedBy}
			value={value}
			readOnly={readOnly}
			spellCheck={false}
			autoCapitalize="off"
			autoCorrect="off"
			onKeyDown={onKeyDown}
			onChange={(e) => {
				setValue(e.target.value)
				onChange(e.target.value)
			}}
			style={{ fontSize }}
			className="min-h-[18rem] w-full rounded-lg border border-slate-300 bg-white p-3 font-mono text-slate-900 outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-400/30 dark:border-white/20"
		/>
	)
}
