"""Run the upstream test suite, judge it from the report, and collect crash diagnostics.

osx-arm64 py3.11-3.13 can lose the interpreter during the suite (pydantic/pydantic#14242),
so a plain exit code is not a reliable verdict. The suite runs with faulthandler and
unbuffered output, and on osx-arm64 it is repeated under the CPython debug allocator, dev
mode and a low GC threshold. A fatal exit prints the signal, the faulthandler traceback and
the newest macOS crash report. Accept the run only when the junit report is complete and
clean.
"""

import json
import os
import platform
import re
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

IS_MACOS_ARM64 = sys.platform == "darwin" and platform.machine() == "arm64"
DEBUG_RUNS = int(os.environ.get("DIAG_RUNS", "2"))

PYTEST = [
    # faulthandler dumps the Python traceback on a fatal signal (asked upstream on
    # pydantic/pydantic#14242); -u keeps the last reported test when the process dies.
    "-X", "faulthandler",
    "-u",
    "-m", "pytest",
    "--ignore=tests/test_docstrings.py",
    "--ignore=tests/test_tzinfo.py",
    "--ignore=tests/validators/test_allow_partial.py",
    "-W", "default::pytest.PytestUnknownMarkWarning",
    "-W", "default::pytest.PytestRemovedIn10Warning",
]


def run(*args, env=None):
    child_env = os.environ.copy()
    child_env["PYTHONFAULTHANDLER"] = "1"
    # gcdebug.py sits next to this script; test commands run with the test dir as cwd.
    child_env["PYTHONPATH"] = os.pathsep.join(
        part for part in (os.getcwd(), os.environ.get("PYTHONPATH")) if part
    )
    if env:
        child_env.update(env)
    return subprocess.run([sys.executable, *args], capture_output=True, text=True, env=child_env)


def describe_exit(tag, result):
    print(f"[{tag}] pytest exited {result.returncode}")
    if result.returncode < 0:
        try:
            name = signal.Signals(-result.returncode).name
        except ValueError:
            name = f"signal {-result.returncode}"
        print(f"[{tag}] killed by {name}")


def dump_output(tag, result):
    print(f"--- [{tag}] stdout tail (last reported tests) ---")
    print(result.stdout[-6000:])
    print(f"--- [{tag}] stderr tail (faulthandler traceback) ---")
    print(result.stderr[-40000:])


def dump_macos_crash_report(tag):
    """Print the faulting-thread stack of the newest macOS crash report, if any."""
    since = time.time() - 600
    dirs = [Path.home() / "Library/Logs/DiagnosticReports", Path("/Library/Logs/DiagnosticReports")]
    reports = [
        report
        for directory in dirs
        if directory.is_dir()
        for pattern in ("python*.ips", "Python*.ips")
        for report in directory.glob(pattern)
        if report.stat().st_mtime >= since
    ]
    if not reports:
        print(f"[{tag}] no macOS crash report in the last 10 minutes")
        return
    report = max(reports, key=lambda path: path.stat().st_mtime)
    print(f"[{tag}] macOS crash report: {report}")
    try:
        _, _, payload = report.read_text().partition("\n")
        data = json.loads(payload)
        images = data.get("usedImages", [])
        faulting = data.get("faultingThread", 0)
        for frame in data["threads"][faulting].get("frames", [])[:60]:
            image = images[frame.get("imageIndex", 0)]
            name = image.get("name") or image.get("path") or "?"
            print(f"[{tag}] {name} {frame.get('symbol', '')} +{frame.get('imageOffset', 0)}")
    except Exception as exc:  # noqa: BLE001 - diagnostics must not hide the verdict
        print(f"[{tag}] crash report parse failed: {exc}")
        print(report.read_text()[:4000])


def main():
    expected = re.search(r"(\d+)(?:/\d+)? tests collected", run(*PYTEST, "--collect-only", "-q").stdout)
    if not expected:
        sys.exit("TEST FAIL: pytest did not collect")
    expected = int(expected.group(1))

    result = run(*PYTEST, "-vv", "--junitxml=junit.xml")
    describe_exit("suite", result)
    if result.returncode != 0:
        dump_output("suite", result)
        dump_macos_crash_report("suite")

    try:
        root = ET.parse("junit.xml").getroot()
    except FileNotFoundError:
        sys.exit("TEST FAIL: junit.xml missing, pytest did not reach session end")
    # a test that reports more than once (module teardown) is counted twice in the report
    tests = sum(int(suite.get("tests")) for suite in root.iter("testsuite"))
    failures = sum(int(suite.get("failures")) for suite in root.iter("testsuite"))
    errors = sum(int(suite.get("errors")) for suite in root.iter("testsuite"))
    print(f"junit: tests={tests} failures={failures} errors={errors} expected={expected}")
    if failures or errors:
        sys.exit("TEST FAIL: report has failures or errors")
    if tests < expected:
        sys.exit("TEST FAIL: report is shorter than the collection, pytest stopped early")
    print("TEST PASS: complete report, no failures")

    if not IS_MACOS_ARM64:
        return
    reproduced = False
    for attempt in range(1, DEBUG_RUNS + 1):
        tag = f"diag{attempt}"
        print(
            f"[{tag}] full suite with PYTHONMALLOC=debug, PYTHONDEVMODE=1, MallocScribble=1, "
            "gc threshold 100,10,10 (pydantic/pydantic#14242)"
        )
        result = run(
            *PYTEST,
            "-p", "gcdebug",
            "-q",
            env={
                "PYTHONMALLOC": "debug",
                "PYTHONDEVMODE": "1",
                "MallocScribble": "1",
                "GC_THRESHOLD": "100,10,10",
            },
        )
        describe_exit(tag, result)
        if result.returncode != 0:
            dump_output(tag, result)
            dump_macos_crash_report(tag)
            reproduced = True
            break
        print(f"[{tag}] clean")
    if reproduced:
        sys.exit("TEST FAIL: diagnostic run reproduced the fatal exit (pydantic/pydantic#14242)")
    print(f"DIAG: not reproduced in {DEBUG_RUNS} debug run(s)")


if __name__ == "__main__":
    main()
