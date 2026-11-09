"""Run the upstream test suite and judge the result from the junit report, not the exit code.

osx-arm64 py3.11-3.13 can lose the interpreter during the suite (pydantic/pydantic#14242), so a
plain exit code is not a reliable verdict. The suite runs with faulthandler and unbuffered output.
On failure, the stdout/stderr tails and the newest macOS crash report are printed. Accept the run
only when the junit report is complete and clean.
"""

import json
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

PYTEST = [
    "-X", "faulthandler",
    "-u",
    "-m", "pytest",
    "--ignore=tests/test_docstrings.py",
    "--ignore=tests/test_tzinfo.py",
    "--ignore=tests/validators/test_allow_partial.py",
    "-W", "default::pytest.PytestUnknownMarkWarning",
    "-W", "default::pytest.PytestRemovedIn10Warning",
]


def run(*args):
    return subprocess.run([sys.executable, *args], capture_output=True, text=True)


def dump_macos_crash_report():
    """Print the faulting-thread stack of the newest macOS crash report from the last 10 minutes."""
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
        print("no macOS crash report in the last 10 minutes")
        return
    report = max(reports, key=lambda path: path.stat().st_mtime)
    print(f"macOS crash report: {report}")
    try:
        _, _, payload = report.read_text().partition("\n")
        data = json.loads(payload)
        print(f"exception={data.get('exception')} termination={data.get('termination')}")
        images = data.get("usedImages", [])
        faulting = data.get("faultingThread", 0)
        for frame in data["threads"][faulting].get("frames", [])[:60]:
            image = images[frame.get("imageIndex", 0)]
            name = image.get("name") or image.get("path") or "?"
            print(f"{name} {frame.get('symbol', '')} +{frame.get('imageOffset', 0)}")
    except Exception as exc:  # noqa: BLE001 - diagnostics must not hide the verdict
        print(f"crash report parse failed: {exc}")


def main():
    expected = re.search(r"(\d+)(?:/\d+)? tests collected", run(*PYTEST, "--collect-only", "-q").stdout)
    if not expected:
        sys.exit("TEST FAIL: pytest did not collect")
    expected = int(expected.group(1))

    result = run(*PYTEST, "-vv", "--junitxml=junit.xml")
    print(f"pytest exited {result.returncode}")
    if result.returncode != 0:
        print("--- stdout tail (last reported tests) ---")
        print(result.stdout[-4000:])
        print("--- stderr tail (faulthandler traceback) ---")
        print(result.stderr[-12000:])
        dump_macos_crash_report()

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


if __name__ == "__main__":
    main()
