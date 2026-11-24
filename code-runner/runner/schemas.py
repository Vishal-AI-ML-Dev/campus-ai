"""Strict request/response schemas for ``POST /v1/executions`` (spec §4.6).

The request carries identifiers only: no limits, flags, images, paths, mounts, runtimes or expected outputs. Unknown
fields are rejected (422). ``hidden_digest`` binds a graded request to the exact hidden test set the backend's public
bundle names; a mismatch is a 409 (pack digest mismatch), never a silent run against other tests.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_SOURCE_BYTES = 64 * 1024
MAX_CUSTOM_INPUT_BYTES = 16 * 1024
MAX_BODY_BYTES = 96 * 1024  # source + custom input + JSON escaping headroom; larger bodies get 413

UUID4_RE = r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
KEY_RE = r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$"
DIGEST_RE = r"^[0-9a-f]{64}$"

Language = Literal["python313", "cpp17", "java21", "c11"]
Kind = Literal["visible", "custom", "graded"]
TestSet = Literal["visible", "hidden"]
Verdict = Literal[
    "accepted", "wrong_answer", "compile_error", "runtime_error", "time_limit", "memory_limit", "output_limit",
    "sandbox_error",
]
FailureClass = Literal["wrong_answer", "runtime_error", "time_limit", "memory_limit", "output_limit"]


class ExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    execution_id: str = Field(pattern=UUID4_RE)
    kind: Kind
    problem_key: str = Field(pattern=KEY_RE)
    problem_version: int = Field(ge=1, le=10_000)
    test_set: TestSet
    language: Language
    source: str = Field(min_length=1)
    custom_input: str | None = None
    hidden_digest: str | None = Field(default=None, pattern=DIGEST_RE)

    @field_validator("source")
    @classmethod
    def _source_size(cls, v: str) -> str:
        if len(v.encode("utf-8")) > MAX_SOURCE_BYTES:
            raise ValueError("source too large")
        if "\x00" in v:
            raise ValueError("source contains NUL")
        return v

    @field_validator("custom_input")
    @classmethod
    def _custom_size(cls, v: str | None) -> str | None:
        if v is not None and len(v.encode("utf-8")) > MAX_CUSTOM_INPUT_BYTES:
            raise ValueError("custom input too large")
        return v

    @model_validator(mode="after")
    def _combination(self) -> "ExecutionRequest":
        if self.kind == "graded":
            if self.test_set != "hidden" or self.hidden_digest is None or self.custom_input is not None:
                raise ValueError("graded runs use the hidden set, need hidden_digest and take no custom input")
        elif self.kind == "visible":
            if self.test_set != "visible" or self.hidden_digest is not None or self.custom_input is not None:
                raise ValueError("visible runs use the visible set only")
        else:  # custom
            if self.test_set != "visible" or self.custom_input is None or self.hidden_digest is not None:
                raise ValueError("custom runs need custom_input and no hidden fields")
        return self


class CompileResult(BaseModel):
    ok: bool
    message_trunc: str | None
    time_ms: int


class VisibleCase(BaseModel):
    id: str
    verdict: Literal["accepted", "wrong_answer", "runtime_error", "time_limit", "memory_limit", "output_limit"]
    stdout_trunc: str
    stderr_trunc: str
    time_ms: int
    memory_kb: int | None


class FirstFailure(BaseModel):
    group: Literal["basic", "edge", "performance"]
    # "class" is the wire name (spec §4.6); the attribute avoids the Python keyword.
    failure_class: FailureClass = Field(serialization_alias="class")


class HiddenSummary(BaseModel):
    groups: dict[str, tuple[int, int]]  # group -> (passed, total)
    first_failure: FirstFailure | None
    max_time_ms: int
    max_memory_kb: int | None


class SandboxErrorInfo(BaseModel):
    error_class: str = Field(serialization_alias="class")


class ExecutionResponse(BaseModel):
    execution_id: str
    runner_version: str
    pack_digest: str
    compile: CompileResult | None
    verdict: Verdict
    visible: list[VisibleCase] | None = None
    hidden_summary: HiddenSummary | None = None
    sandbox_error: SandboxErrorInfo | None = None

    def wire(self) -> dict:
        return self.model_dump(by_alias=True, exclude_none=False)
