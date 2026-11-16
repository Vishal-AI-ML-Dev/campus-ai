/**
 * Campus AI - Student AI Mentor chat page.
 *
 * Place this file at: src/app/dashboard/mentor/page.tsx
 *
 * A chat UI for the AI Career Mentor. Every answer is grounded ONLY on the
 * student's VERIFIED profile (verified skills, verified project contributions,
 * attendance %, CGPA) - the backend builds that context, so advice can't be
 * based on unproven claims.
 *
 * Streaming (Batch 10a): answers arrive token-by-token over Server-Sent Events.
 * Memory (Batch 10b): conversations are saved server-side. A sidebar lists past
 * threads; selecting one loads its transcript, and follow-ups continue it (the
 * backend replays prior turns to the worker). "New chat" starts a fresh thread.
 *
 * Backend endpoints:
 *   GET    /mentor/conversations                 -> [{ id, title, updated_at }]
 *   GET    /mentor/conversations/{id}            -> { ..., messages: [...] }
 *   DELETE /mentor/conversations/{id}            -> 204
 *   POST   /mentor/chat/stream                   -> SSE token stream
 *     body -> { question, conversation_id? }
 *     SSE  -> data: {"type":"meta","conversation_id":N}   (first)
 *             data: {"type":"token","content":"..."}       (many)
 *             data: {"type":"done","provider":"groq"}      (once)
 *             data: {"type":"error","detail":"..."}        (on failure)
 */

"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { api, ApiError } from "@/lib/api"

type Turn = {
	role: "user" | "assistant"
	content: string
}

type ConversationSummary = {
	id: number
	title: string
	created_at: string
	updated_at: string
}

type MessageOut = {
	id: number
	role: "user" | "assistant"
	content: string
	provider: string | null
	created_at: string
}

type ConversationDetail = ConversationSummary & { messages: MessageOut[] }

const SUGGESTIONS = [
	"Backend role ke liye mera skill gap kya hai?",
	"Mere verified projects ke hisab se kaunsi jobs suit karengi?",
	"Placement ke liye agle 30 din ka plan banao.",
]

export default function MentorPage() {
	const [conversations, setConversations] = useState<ConversationSummary[]>([])
	const [activeId, setActiveId] = useState<number | null>(null)
	const [messages, setMessages] = useState<Turn[]>([])
	const [input, setInput] = useState("")
	const [loading, setLoading] = useState(false)
	const [error, setError] = useState<string | null>(null)
	const [provider, setProvider] = useState<string | null>(null)
	const endRef = useRef<HTMLDivElement | null>(null)

	// Keep the latest message in view as the conversation grows / streams.
	useEffect(() => {
		endRef.current?.scrollIntoView({ behavior: "smooth" })
	}, [messages, loading])

	const refreshConversations = useCallback(async () => {
		try {
			const list = await api.get<ConversationSummary[]>(
				"/mentor/conversations",
			)
			setConversations(list)
		} catch {
			// Non-fatal: the sidebar just stays as-is.
		}
	}, [])

	useEffect(() => {
		refreshConversations()
	}, [refreshConversations])

	function newChat() {
		if (loading) return
		setActiveId(null)
		setMessages([])
		setProvider(null)
		setError(null)
	}

	async function selectConversation(id: number) {
		if (loading || id === activeId) return
		setError(null)
		setProvider(null)
		try {
			const detail = await api.get<ConversationDetail>(
				`/mentor/conversations/${id}`,
			)
			setActiveId(id)
			setMessages(
				detail.messages.map((m) => ({ role: m.role, content: m.content })),
			)
		} catch (err) {
			setError(
				err instanceof ApiError
					? err.message
					: "Conversation load nahi ho paayi.",
			)
		}
	}

	async function deleteConversation(id: number, e: React.MouseEvent) {
		e.stopPropagation()
		if (loading) return
		try {
			await api.delete(`/mentor/conversations/${id}`)
			if (id === activeId) newChat()
			await refreshConversations()
		} catch {
			// Ignore: a failed delete simply leaves the thread in place.
		}
	}

	async function send(question: string) {
		const trimmed = question.trim()
		if (!trimmed || loading) return
		setError(null)
		// Optimistically add the user's turn + an empty assistant turn we grow as
		// tokens stream in.
		setMessages((prev) => [
			...prev,
			{ role: "user", content: trimmed },
			{ role: "assistant", content: "" },
		])
		setInput("")
		setLoading(true)
		try {
			const body: Record<string, unknown> = { question: trimmed }
			if (activeId !== null) body.conversation_id = activeId
			const { provider: p, conversationId } = await api.stream(
				"/mentor/chat/stream",
				body,
				{
					onToken: (text) => {
						setMessages((prev) => {
							const next = [...prev]
							const last = next[next.length - 1]
							if (last && last.role === "assistant") {
								next[next.length - 1] = {
									...last,
									content: last.content + text,
								}
							}
							return next
						})
					},
				},
			)
			if (p) setProvider(p)
			if (conversationId !== null) setActiveId(conversationId)
			// Pick up the new/updated thread (title + recency) in the sidebar.
			await refreshConversations()
		} catch (err) {
			// Drop the empty assistant bubble on failure, then show the error.
			setMessages((prev) => {
				const next = [...prev]
				const last = next[next.length - 1]
				if (last && last.role === "assistant" && last.content === "") {
					next.pop()
				}
				return next
			})
			setError(
				err instanceof ApiError
					? err.message
					: "AI mentor is unavailable right now.",
			)
		} finally {
			setLoading(false)
		}
	}

	function onSubmit(e: React.FormEvent) {
		e.preventDefault()
		send(input)
	}

	return (
		<div className="flex h-[calc(100vh-8rem)] gap-4">
			{/* Sidebar: saved conversations */}
			<aside className="hidden w-64 shrink-0 flex-col rounded-2xl border border-slate-200 dark:border-white/10 bg-white dark:bg-slate-900 p-3 md:flex">
				<button
					type="button"
					onClick={newChat}
					disabled={loading}
					className="mb-3 w-full rounded-lg bg-gradient-to-r from-indigo-500 to-violet-500 px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
				>
					+ New chat
				</button>
				<div className="flex-1 space-y-1 overflow-y-auto">
					{conversations.length === 0 ? (
						<p className="px-2 py-4 text-center text-xs text-slate-400">
							Abhi koi saved chat nahi.
						</p>
					) : (
						conversations.map((c) => (
							<div
								key={c.id}
								onClick={() => selectConversation(c.id)}
								className={`group flex cursor-pointer items-center justify-between rounded-lg px-2 py-2 text-sm ${
									c.id === activeId
										? "bg-indigo-50 text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300"
									: "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
								}`}
							>
								<span className="truncate">{c.title}</span>
								<button
									type="button"
									onClick={(e) => deleteConversation(c.id, e)}
									disabled={loading}
									aria-label="Delete conversation"
									className="ml-2 hidden shrink-0 text-slate-400 hover:text-red-500 group-hover:block"
								>
									×
								</button>
							</div>
						))
					)}
				</div>
			</aside>

			{/* Main chat column */}
			<div className="flex min-w-0 flex-1 flex-col">
				<div className="flex items-center justify-between">
					<div>
						<h2 className="text-2xl font-bold">AI Career Mentor</h2>
						<p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
							Grounded only on your verified profile - skills, projects,
							attendance, and CGPA.
						</p>
					</div>
					<div className="flex items-center gap-2">
						{/* New chat is always reachable on small screens (sidebar hidden). */}
						<button
							type="button"
							onClick={newChat}
							disabled={loading}
							className="rounded-lg border border-slate-200 dark:border-white/10 px-3 py-1.5 text-xs text-slate-600 dark:text-slate-300 hover:border-indigo-400 disabled:opacity-50 md:hidden"
						>
							+ New
						</button>
						{provider && (
							<span className="rounded-full bg-slate-100 dark:bg-slate-800 px-2 py-0.5 text-xs text-slate-500 dark:text-slate-400">
								{provider}
							</span>
						)}
					</div>
				</div>

				{/* Conversation */}
				<div className="mt-4 flex-1 space-y-3 overflow-y-auto rounded-2xl border border-slate-200 dark:border-white/10 bg-white dark:bg-slate-900 p-4">
					{messages.length === 0 && !loading ? (
						<div className="flex h-full flex-col items-center justify-center text-center">
							<p className="text-slate-500 dark:text-slate-400">
								Apne career, skills ya placement ke baare me kuch bhi poochho.
							</p>
							<div className="mt-4 flex flex-wrap justify-center gap-2">
								{SUGGESTIONS.map((s) => (
									<button
										key={s}
										type="button"
										onClick={() => send(s)}
										className="rounded-full border border-slate-200 dark:border-white/10 bg-white dark:bg-slate-900 px-3 py-1.5 text-xs text-slate-600 dark:text-slate-300 hover:border-indigo-400 hover:text-slate-900 dark:hover:text-white"
									>
										{s}
									</button>
								))}
							</div>
						</div>
					) : (
						messages.map((turn, index) => {
							const isLast = index === messages.length - 1
							const isStreaming =
								loading && isLast && turn.role === "assistant"
							return (
								<div
									key={index}
									className={`flex ${
										turn.role === "user" ? "justify-end" : "justify-start"
									}`}
								>
									<div
										className={`max-w-[80%] whitespace-pre-wrap rounded-2xl px-4 py-2 text-sm ${
											turn.role === "user"
												? "bg-gradient-to-r from-indigo-500 to-violet-500 text-white"
												: "bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-200"
										}`}
									>
										{turn.content ||
											(isStreaming ? "Mentor soch raha hai..." : "")}
										{isStreaming && turn.content && (
											<span className="ml-0.5 inline-block animate-pulse">▍</span>
										)}
									</div>
								</div>
							)
						})
					)}

					<div ref={endRef} />
				</div>

				{error && (
					<p className="mt-3 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
						{error}
					</p>
				)}

				{/* Composer */}
				<form onSubmit={onSubmit} className="mt-3 flex gap-2">
					<input
						value={input}
						onChange={(e) => setInput(e.target.value)}
						placeholder="Apna sawaal type karo..."
						disabled={loading}
						className="flex-1 rounded-lg border border-slate-200 dark:border-white/10 bg-white dark:bg-slate-900 px-3 py-2 text-sm outline-none focus:border-indigo-400 disabled:opacity-60"
					/>
					<button
						type="submit"
						disabled={loading || !input.trim()}
						className="rounded-lg bg-gradient-to-r from-indigo-500 to-violet-500 px-5 py-2 text-sm font-medium text-white disabled:opacity-50"
					>
						Send
					</button>
				</form>
			</div>
		</div>
	)
}
