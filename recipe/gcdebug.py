"""Diagnostic pytest plugin for pydantic/pydantic#14242.

Loaded only by recipe/run_tests.py on osx-arm64 via `-p gcdebug`. It raises GC pressure and
forces collections while the interpreter is still healthy, so a GC/clear bug in
pydantic-core fails under faulthandler instead of losing the process silently.
"""

import gc
import os


def pytest_configure(config):
    threshold = os.environ.get("GC_THRESHOLD", "100,10,10")
    gc.set_threshold(*(int(part) for part in threshold.split(",")))
    print(f"[gcdebug] threshold={gc.get_threshold()}")


def pytest_runtest_teardown(item, nextitem):
    # Collect while the state from the suspect module is still around.
    path = str(getattr(item, "path", "") or getattr(item, "fspath", ""))
    if path.endswith(os.path.join("tests", "validators", "test_with_default.py")):
        gc.collect()


def pytest_unconfigure(config):
    # Exercise collection before interpreter shutdown; faulthandler is active.
    print(f"[gcdebug] counts={gc.get_count()} stats={gc.get_stats()}")
    gc.collect()
    print("[gcdebug] final gc.collect() done")
