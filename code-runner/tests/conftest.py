import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # the runner package
sys.path.insert(0, str(HERE))  # fakes, synthetic

from synthetic import build_pack  # noqa: E402

from runner.pack import load_pack  # noqa: E402


@pytest.fixture
def pack_dir(tmp_path):
    root = tmp_path / "pack"
    build_pack(root)
    return root


@pytest.fixture
def pack(pack_dir):
    # Read-only enforcement is tested separately; tmp dirs are writable by the test user.
    return load_pack(pack_dir, require_read_only=False)
