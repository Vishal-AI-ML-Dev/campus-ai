"""Fixed language definitions (P8-D1, P8-S3). Nothing here can be influenced by a request.

Every command is an argument array (no shell, no string interpolation of request data). Compiler and runtime flags
are fixed; the request schema has no field for flags, images, limits or paths.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import pins

WORKSPACE = "/workspace"
SRC_DIR = f"{WORKSPACE}/src"
BUILD_DIR = f"{WORKSPACE}/build"
RUN_DIR = f"{WORKSPACE}/run"
# Compiler scratch space (gcc/g++ write temporaries to $TMPDIR). /tmp is read-only, so the compile exec points TMPDIR
# here: builder-owned, inside the /workspace tmpfs, and never part of the build tar (only src/ and build/ are copied).
COMPILE_TMP_DIR = f"{WORKSPACE}/tmp"
BUILDER_UID = 65533  # owns src/ and build/ (made read-only before tests run)
RUN_UID = 65534  # runs the student's program; cannot modify src/ or build/


@dataclass(frozen=True)
class Language:
    name: str
    image: str
    source_file: str
    compile_cmd: tuple[str, ...]
    run_cmd: tuple[str, ...]
    cpu_multiplier: int  # x problem time_ms (§4.4: C/C++ 1, Java 2, Python 3)
    compile_wall_s: int
    compile_memory_mb: int
    run_memory_mb: dict[str, int]  # memory profile -> MB
    pids: int
    nofile: int
    # stderr markers that mean the runtime ran out of memory inside its own limit (heap cap below the cgroup).
    memory_markers: tuple[bytes, ...] = ()


def _java_run(memory_mb: int) -> tuple[str, ...]:
    return (
        "java", "-Xss64m", "-XX:+UseSerialGC", f"-Xmx{memory_mb - 64}m", "-XX:-UsePerfData",
        f"-Djava.io.tmpdir={RUN_DIR}", "-cp", BUILD_DIR, "Main",
    )


LANGUAGES: dict[str, Language] = {
    "c11": Language(
        name="c11",
        image=pins.IMAGE_GCC,
        source_file="main.c",
        compile_cmd=("gcc", "-O2", "-std=c11", "-o", f"{BUILD_DIR}/main", f"{SRC_DIR}/main.c", "-lm"),
        run_cmd=(f"{BUILD_DIR}/main",),
        cpu_multiplier=1,
        compile_wall_s=10,
        compile_memory_mb=512,
        run_memory_mb={"standard": 256, "large": 512},
        pids=64,
        nofile=64,
    ),
    "cpp17": Language(
        name="cpp17",
        image=pins.IMAGE_GCC,
        source_file="main.cpp",
        compile_cmd=("g++", "-O2", "-std=c++17", "-o", f"{BUILD_DIR}/main", f"{SRC_DIR}/main.cpp"),
        run_cmd=(f"{BUILD_DIR}/main",),
        cpu_multiplier=1,
        compile_wall_s=10,
        compile_memory_mb=512,
        run_memory_mb={"standard": 256, "large": 512},
        pids=64,
        nofile=64,
        memory_markers=(b"std::bad_alloc",),
    ),
    "java21": Language(
        name="java21",
        image=pins.IMAGE_JAVA,
        source_file="Main.java",
        # -proc:none: no annotation processors, so compilation cannot run code from the classpath.
        compile_cmd=("javac", "-proc:none", "-J-Xmx384m", "-d", BUILD_DIR, f"{SRC_DIR}/Main.java"),
        run_cmd=(),  # built per memory profile by run_command()
        cpu_multiplier=2,
        compile_wall_s=15,
        compile_memory_mb=768,
        run_memory_mb={"standard": 512, "large": 768},
        pids=128,
        nofile=256,
        memory_markers=(b"java.lang.OutOfMemoryError",),
    ),
    "python313": Language(
        name="python313",
        image=pins.IMAGE_PYTHON,
        source_file="main.py",
        # Syntax check only; it parses, never executes, the source.
        compile_cmd=(
            "python3", "-I", "-S", "-c",
            "import ast; ast.parse(open('/workspace/src/main.py', 'rb').read(), 'main.py')",
        ),
        run_cmd=("python3", "-I", "-S", f"{SRC_DIR}/main.py"),
        cpu_multiplier=3,
        compile_wall_s=5,
        compile_memory_mb=256,
        run_memory_mb={"standard": 256, "large": 512},
        pids=64,
        nofile=64,
        memory_markers=(b"MemoryError",),
    ),
}


def run_command(lang: Language, memory_profile: str) -> tuple[str, ...]:
    if lang.name == "java21":
        return _java_run(lang.run_memory_mb[memory_profile])
    return lang.run_cmd
