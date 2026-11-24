"""Static guards over the runner source (spec §11.6, P8-S5)."""

import ast
import re
from pathlib import Path

RUNNER = Path(__file__).resolve().parent.parent / "runner"
SOURCES = {p.name: p.read_text(encoding="utf-8") for p in RUNNER.glob("*.py")}


def test_no_shell_and_no_dynamic_code():
    for name, src in SOURCES.items():
        assert "shell=True" not in src, name
        assert "os.system" not in src and "os.popen" not in src, name
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in ("eval", "exec", "compile", "__import__"), (name, node.func.id)


def test_only_the_sandbox_and_self_check_start_processes():
    users = {name for name, src in SOURCES.items() if re.search(r"\bsubprocess\b", src)}
    assert users == {"sandbox.py"}


def test_no_third_party_telemetry_or_unexpected_imports():
    allowed_third_party = {"fastapi", "pydantic"}
    stdlib = {"__future__", "asyncio", "collections", "concurrent", "contextlib", "dataclasses", "hashlib", "hmac",
              "io", "json", "logging", "math", "os", "re", "secrets", "shutil", "subprocess", "sys", "tarfile",
              "threading", "time", "typing", "pathlib"}
    for name, src in SOURCES.items():
        for node in ast.walk(ast.parse(src)):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                mods = [node.module or ""]
            for mod in mods:
                top = mod.split(".")[0]
                assert top in stdlib | allowed_third_party, (name, mod)


def test_backend_and_worker_never_import_the_runner():
    root = RUNNER.parent.parent
    for folder in ("backend/app", "ai-worker"):
        for path in (root / folder).rglob("*.py"):
            if ".venv" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            assert "from runner" not in text and "import runner" not in text, path


def test_no_hidden_test_material_in_the_runner_tree():
    """Only synthetic material lives here: no .in/.out files, no packs, no reference solutions of real problems."""
    tree = RUNNER.parent
    real_keys = {p.name for p in (tree.parent / "backend/app/coding_bank/problems").iterdir()}
    for path in tree.rglob("*"):
        if ".venv" in path.parts or "__pycache__" in path.parts:
            continue
        assert path.suffix not in (".in", ".out", ".tar"), path
        assert path.name not in real_keys, path
