"""Performance work must not change pixels: invariants of the optimised paths."""
import os
import subprocess
import sys

import pytest

from csp_headless.engine import Engine
from csp_headless.render import composite


@pytest.fixture
def e(tmp_path):
    engine = Engine(tmp_path / "state")
    engine.call("paint_document", action="create", width=320, height=240, background="#F0E8D8")
    engine.call("paint_layer", action="create", name="a")
    engine.call("paint_layer", action="create", name="b")
    return engine


def state(e):
    return composite(e.doc).tobytes()


# --- 1. OpenBLAS -------------------------------------------------------------------------------------

@pytest.mark.parametrize("preset,expected", [(None, "1"), ("4", "4")])
def test_openblas_threads_default_to_one(preset, expected):
    env = {k: v for k, v in os.environ.items() if k != "OPENBLAS_NUM_THREADS"}
    if preset: env["OPENBLAS_NUM_THREADS"] = preset
    code = "import os, csp_headless.engine; print(os.environ['OPENBLAS_NUM_THREADS'])"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, check=True).stdout
    assert out.strip() == expected
