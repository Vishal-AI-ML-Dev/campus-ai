"""One execution: compile in a fresh sandbox, run the tests in a fresh sandbox, judge, reduce, clean up.

TRUSTED LAYER. Flow (spec §4.5):

1. resolve ``problem_key@problem_version`` in the installed pack; a graded request must name the pack's exact hidden
   test-set digest (else ``PackMismatch`` -> 409);
2. compile container (compile limits, builder uid): write the source from stdin, compile with the fixed command,
   make ``src/`` and ``build/`` read-only, export them as an opaque tar held in runner memory, remove the container;
3. run container (run limits, run uid): restore the tar (owned by the builder uid, read-only for the run uid), then
   for each test stream its stdin from runner memory, capture at most 64 KB per stream, enforce the per-test wall
   clock and the 30 s submission budget;
4. on any limit, kill the WHOLE container (cgroup kill = process-tree kill) and continue in a new container while
   budget remains; after a normal exit, kill every remaining run-uid process and wipe run-uid files;
5. judge outputs with the problem's checker (here, never in the sandbox), reduce hidden results to group counts;
6. remove every container in ``finally``; a failure to remove is left to the reaper.

U4: tests not run because the 30 s budget ran out count as ``time_limit`` (the candidate's code used the budget).
Any runner/infrastructure fault raises ``SandboxFault`` and the whole execution becomes ``sandbox_error`` (incomplete,
never a candidate verdict).
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from . import packformat as pf
from .checkers import check
from .languages import BUILD_DIR, BUILDER_UID, LANGUAGES, RUN_DIR, RUN_UID, SRC_DIR, WORKSPACE, Language, run_command
from .limits import (
    DISPLAY_COMPILE_BYTES,
    DISPLAY_STDERR_BYTES,
    DISPLAY_STDOUT_BYTES,
    OUTPUT_CAP_BYTES,
    SUBMISSION_WALL_S,
    TestLimits,
    round_ms,
    test_limits,
)
from .pack import InstalledPack, PackMismatch
from .sandbox import ContainerSpec, DockerCLI, ExecResult, SandboxFault, container_argv
from .schemas import (
    CompileResult,
    ExecutionRequest,
    ExecutionResponse,
    FirstFailure,
    HiddenSummary,
    SandboxErrorInfo,
    VisibleCase,
)

RUNNER_VERSION = "1.0.0"
MAX_BUILD_TAR_BYTES = 24 * 1024 * 1024
_EXEC_INFRA_CODES = {125, 126, 127}
_KILL_CODES = {137, 152}  # SIGKILL, SIGXCPU (RLIMIT_CPU)
# Fixed cleanup script: kill every process of the run uid (except PID 1 and itself) and wipe its files.
_CLEANUP = (
    "sh", "-c",
    "kill -9 -1 2>/dev/null; "
    f"find {WORKSPACE} -mindepth 1 -maxdepth 1 -user {RUN_UID} -exec rm -rf {{}} + 2>/dev/null; "
    f"mkdir -p {RUN_DIR}",
)
_RUN_ENV = {"HOME": RUN_DIR, "LANG": "C.UTF-8", "TMPDIR": RUN_DIR}


@dataclass
class Case:
    group: str  # "visible", "custom", or a hidden group
    name: str
    input: bytes
    expected: bytes | None


@dataclass
class Outcome:
    case: Case
    verdict: str
    wall_ms: float = 0.0
    stdout: bytes = b""
    stderr: bytes = b""
    ran: bool = True


@dataclass
class ExecutionLog:
    """The ONLY fields that may be logged or turned into metrics (content-free, spec §4.8)."""

    execution_id: str
    request_id: str
    language: str
    kind: str
    verdict: str
    outcome: str  # completed | sandbox_error
    compile_ms: int = 0
    run_ms: int = 0
    limit_hit: str = "none"
    error_class: str = "none"
    pack_digest: str = ""
    tests_run: int = 0


@dataclass
class _Ctx:
    req: ExecutionRequest
    lang: Language
    limits: TestLimits
    deadline: float
    containers: list[str] = field(default_factory=list)


def _scrub(data: bytes, limit: int) -> str:
    text = data[:limit].decode("utf-8", "replace")
    for prefix in (f"{SRC_DIR}/", f"{BUILD_DIR}/", f"{RUN_DIR}/", f"{WORKSPACE}/"):
        text = text.replace(prefix, "")
    return text


class Executor:
    def __init__(
        self,
        docker: DockerCLI,
        pack: InstalledPack,
        clock: Callable[[], float] = time.monotonic,
        on_container_left: Callable[[str], None] | None = None,
    ):
        self.docker = docker
        self.pack = pack
        self.clock = clock
        self.on_container_left = on_container_left or (lambda cid: None)

    # ------------------------------------------------------------------ public
    def run(self, req: ExecutionRequest, request_id: str) -> tuple[ExecutionResponse, ExecutionLog]:
        ps = self.pack.problem(req.problem_key, req.problem_version)
        if req.kind == "graded" and req.hidden_digest != ps.hidden_digest:
            raise PackMismatch()
        lang = LANGUAGES[req.language]
        limits = test_limits(lang, ps.lock["time_ms"], ps.lock["memory_profile"])
        ctx = _Ctx(req=req, lang=lang, limits=limits, deadline=self.clock() + SUBMISSION_WALL_S)
        log = ExecutionLog(
            execution_id=req.execution_id, request_id=request_id, language=req.language, kind=req.kind,
            verdict="sandbox_error", outcome="sandbox_error", pack_digest=self.pack.digest,
        )
        try:
            compile_result, build = self._compile(ctx)
            log.compile_ms = compile_result.time_ms
            if not compile_result.ok:
                log.verdict, log.outcome = "compile_error", "completed"
                return self._response(req, compile_result, "compile_error"), log
            assert build is not None
            cases = self._cases(req, ps)
            outcomes = self._run_cases(ctx, build, cases)
            log.run_ms = round_ms(sum(o.wall_ms for o in outcomes))
            log.tests_run = sum(o.ran for o in outcomes)
            limit = next((o.verdict for o in outcomes if o.verdict.endswith("_limit")), None)
            log.limit_hit = limit.removesuffix("_limit") if limit else "none"
            verdict = next((o.verdict for o in outcomes if o.verdict != "accepted"), "accepted")
            log.verdict, log.outcome = verdict, "completed"
            return self._response(req, compile_result, verdict, ps, outcomes), log
        except SandboxFault as fault:
            log.verdict, log.outcome, log.error_class = "sandbox_error", "sandbox_error", fault.error_class
            return (
                ExecutionResponse(
                    execution_id=req.execution_id, runner_version=RUNNER_VERSION, pack_digest=self.pack.digest,
                    compile=None, verdict="sandbox_error", sandbox_error=SandboxErrorInfo(error_class=fault.error_class),
                ),
                log,
            )
        finally:
            for cid in ctx.containers:
                if not self.docker.remove(cid):
                    self.on_container_left(cid)

    # ------------------------------------------------------------------ containers
    def _new_container(self, ctx: _Ctx, *, compile_step: bool) -> str:
        lang = ctx.lang
        spec = ContainerSpec(
            image=lang.image,
            memory_mb=lang.compile_memory_mb if compile_step else ctx.limits.memory_mb,
            pids=lang.pids,
            nofile=max(lang.nofile, 256) if compile_step else lang.nofile,
            cpu_rlimit_s=lang.compile_wall_s if compile_step else ctx.limits.cpu_rlimit_s,
            # The container's own processes (init + keep-alive) always run as the builder uid, so the run uid's
            # `kill -9 -1` cleanup -- or a hostile program -- can never stop the container itself.
            user=BUILDER_UID,
            name=f"cr-{'c' if compile_step else 'r'}-{secrets.token_hex(8)}",
            created_epoch=int(time.time()),
        )
        cid = self.docker.create(container_argv(spec))
        ctx.containers.append(cid)
        return cid

    def _must(self, r: ExecResult, error_class: str) -> ExecResult:
        if r.exit_code != 0 or r.killed or r.timed_out:
            raise SandboxFault(error_class)
        return r

    # ------------------------------------------------------------------ compile
    def _compile(self, ctx: _Ctx) -> tuple[CompileResult, bytes | None]:
        d, lang = self.docker, ctx.lang
        start = self.clock()
        cid = self._new_container(ctx, compile_step=True)
        self._must(d.exec(cid, BUILDER_UID, ["mkdir", "-p", SRC_DIR, BUILD_DIR], b"", 15, 4096), "exec_failed")
        self._must(
            d.exec(
                cid, BUILDER_UID, ["dd", f"of={SRC_DIR}/{lang.source_file}", "bs=65536", "status=none"],
                ctx.req.source.encode("utf-8"), 15, 4096,
            ),
            "exec_failed",
        )
        budget = min(lang.compile_wall_s, ctx.deadline - self.clock())
        r = d.exec(cid, BUILDER_UID, list(lang.compile_cmd), b"", max(budget, 0.1), DISPLAY_COMPILE_BYTES * 4)
        elapsed = round_ms((self.clock() - start) * 1000)
        if r.timed_out:
            return CompileResult(ok=False, message_trunc="Compilation exceeded the time limit.", time_ms=elapsed), None
        if r.stdout_overflow or r.stderr_overflow:
            msg = _scrub(r.stderr or r.stdout, DISPLAY_COMPILE_BYTES) + "\n[compiler output truncated]"
            return CompileResult(ok=False, message_trunc=msg, time_ms=elapsed), None
        if r.exit_code in _EXEC_INFRA_CODES and not self._healthy(cid):
            raise SandboxFault("exec_failed")
        if r.exit_code != 0:
            st = d.state(cid)
            if st.oom_killed:
                return CompileResult(ok=False, message_trunc="Compilation ran out of memory.", time_ms=elapsed), None
            return CompileResult(ok=False, message_trunc=_scrub(r.stderr or r.stdout, DISPLAY_COMPILE_BYTES), time_ms=elapsed), None
        self._must(d.exec(cid, BUILDER_UID, ["chmod", "-R", "a-w", SRC_DIR, BUILD_DIR], b"", 15, 4096), "exec_failed")
        tar = d.exec(cid, BUILDER_UID, ["tar", "-C", WORKSPACE, "-cf", "-", "src", "build"], b"", 30, MAX_BUILD_TAR_BYTES)
        if tar.stdout_overflow:
            return CompileResult(ok=False, message_trunc="The compiled program is too large.", time_ms=elapsed), None
        self._must(tar, "exec_failed")
        if d.remove(cid):
            ctx.containers.remove(cid)
        return CompileResult(ok=True, message_trunc=None, time_ms=elapsed), tar.stdout

    def _healthy(self, cid: str) -> bool:
        st = self.docker.state(cid)
        if not (st.exists and st.running):
            return False
        probe = self.docker.exec(cid, RUN_UID, ["true"], b"", 10, 1024)
        return probe.exit_code == 0 and not probe.killed

    # ------------------------------------------------------------------ tests
    def _cases(self, req: ExecutionRequest, ps: pf.ProblemSet) -> list[Case]:
        if req.kind == "custom":
            assert req.custom_input is not None
            return [Case("custom", "custom", req.custom_input.encode("utf-8"), None)]
        if req.kind == "visible":
            return [Case("visible", f"v{i}", t.input, t.output) for i, t in enumerate(ps.groups[pf.VISIBLE_GROUP], 1)]
        return [Case(g, t.name, t.input, t.output) for g in pf.HIDDEN_GROUPS for t in ps.groups[g]]

    def _start_run_container(self, ctx: _Ctx, build: bytes) -> str:
        cid = self._new_container(ctx, compile_step=False)
        self._must(self.docker.exec(cid, BUILDER_UID, ["tar", "-C", WORKSPACE, "-xf", "-"], build, 30, 4096), "restore_failed")
        self._must(self.docker.exec(cid, RUN_UID, ["mkdir", "-p", RUN_DIR], b"", 15, 4096), "restore_failed")
        return cid

    def _drop(self, ctx: _Ctx, cid: str) -> None:
        if self.docker.remove(cid):
            ctx.containers.remove(cid)

    def _run_cases(self, ctx: _Ctx, build: bytes, cases: list[Case]) -> list[Outcome]:
        d, lang = self.docker, ctx.lang
        cmd = list(run_command(lang, self.pack.problems[ctx.req.problem_key].lock["memory_profile"]))
        outcomes: list[Outcome] = []
        cid: str | None = None
        checker = self.pack.problems[ctx.req.problem_key].lock["checker"]
        for case in cases:
            remaining = ctx.deadline - self.clock()
            if remaining <= 0.05:  # U4: the candidate's code used up the submission budget
                outcomes.append(Outcome(case, "time_limit", ran=False))
                continue
            if cid is None:
                cid = self._start_run_container(ctx, build)
            r = d.exec(cid, RUN_UID, cmd, case.input, min(ctx.limits.wall_s, remaining), OUTPUT_CAP_BYTES, RUN_DIR, _RUN_ENV)
            verdict, container_lost = self._classify(cid, r, lang)
            if verdict == "accepted" and case.expected is not None and not check(checker, case.expected, r.stdout):
                verdict = "wrong_answer"
            outcomes.append(Outcome(case, verdict, r.wall_ms, r.stdout, r.stderr))
            if container_lost or r.killed:
                self._drop(ctx, cid)
                cid = None
                continue
            cleanup = d.exec(cid, RUN_UID, list(_CLEANUP), b"", 10, 4096)
            if cleanup.exit_code != 0 or cleanup.killed or cleanup.timed_out:
                d.kill(cid)  # could not prove a clean state: discard the container
                self._drop(ctx, cid)
                cid = None
        return outcomes

    def _classify(self, cid: str, r: ExecResult, lang: Language) -> tuple[str, bool]:
        """Return (verdict, container_lost). Raises SandboxFault for infrastructure failures."""
        if r.stdout_overflow or r.stderr_overflow:
            return "output_limit", True
        if r.timed_out:
            return "time_limit", True
        if r.exit_code is None:
            raise SandboxFault("exec_failed")
        st = self.docker.state(cid)
        if st.oom_killed:
            return "memory_limit", True
        lost = not (st.exists and st.running)
        if r.exit_code == 0:
            return "accepted", lost
        if r.exit_code in _EXEC_INFRA_CODES and not self._healthy(cid):
            raise SandboxFault("exec_failed")
        if any(m in r.stderr for m in lang.memory_markers):
            return "memory_limit", lost
        if r.exit_code in _KILL_CODES:
            return "time_limit", lost
        return "runtime_error", lost

    # ------------------------------------------------------------------ response
    def _response(
        self,
        req: ExecutionRequest,
        compile_result: CompileResult,
        verdict: str,
        ps: pf.ProblemSet | None = None,
        outcomes: list[Outcome] | None = None,
    ) -> ExecutionResponse:
        visible = hidden = None
        if outcomes is not None and req.test_set == "visible":
            visible = [
                VisibleCase(
                    id=o.case.name,
                    verdict=o.verdict,
                    stdout_trunc=_scrub(o.stdout, DISPLAY_STDOUT_BYTES),
                    stderr_trunc=_scrub(o.stderr, DISPLAY_STDERR_BYTES),
                    time_ms=round_ms(o.wall_ms),
                    memory_kb=None,
                )
                for o in outcomes
            ]
        elif outcomes is not None and ps is not None:
            hidden = reduce_hidden(ps, outcomes)
        return ExecutionResponse(
            execution_id=req.execution_id, runner_version=RUNNER_VERSION, pack_digest=self.pack.digest,
            compile=compile_result, verdict=verdict, visible=visible, hidden_summary=hidden,
        )


def reduce_hidden(ps: pf.ProblemSet, outcomes: list[Outcome]) -> HiddenSummary:
    """Hidden results leave the runner ONLY as per-group (passed, total), the first failing group and its class,
    and the maximum time rounded to 10 ms. No inputs, expected or actual outputs, stderr, exit codes or test ids."""
    groups = {g: [0, ps.lock["groups"][g]] for g in pf.HIDDEN_GROUPS}
    first: FirstFailure | None = None
    max_ms = 0.0
    for o in outcomes:
        if o.verdict == "accepted":
            groups[o.case.group][0] += 1
        elif first is None:
            first = FirstFailure(group=o.case.group, failure_class=o.verdict)
        if o.ran:
            max_ms = max(max_ms, o.wall_ms)
    return HiddenSummary(
        groups={g: (p, t) for g, (p, t) in groups.items()}, first_failure=first, max_time_ms=round_ms(max_ms),
        max_memory_kb=None,
    )
