"""Run the upstream test suite and judge the result from the report, not the exit code.

osx-arm64 py3.11 can lose the interpreter after the last test reported (pydantic/pydantic#14242),
so the pytest exit code is not a reliable verdict there. Accept the run only when the junit
report is complete and clean.
"""

import re
import subprocess
import sys
import xml.etree.ElementTree as ET

PYTEST = [
    "-m", "pytest",
    "--ignore=tests/test_docstrings.py",
    "--ignore=tests/test_tzinfo.py",
    "--ignore=tests/validators/test_allow_partial.py",
    "--deselect", "tests/validators/test_with_default.py::test_default_validate_default_after_validator_field_name",
    "-W", "default::pytest.PytestUnknownMarkWarning",
    "-W", "default::pytest.PytestRemovedIn10Warning",
]


def run(*args):
    return subprocess.run([sys.executable, *args], capture_output=True, text=True)


def main():
    expected = re.search(r"(\d+)(?:/\d+)? tests collected", run(*PYTEST, "--collect-only", "-q").stdout)
    if not expected:
        sys.exit("TEST FAIL: pytest did not collect")
    expected = int(expected.group(1))

    result = run(*PYTEST, "-vv", "--junitxml=junit.xml")
    print(f"pytest exited {result.returncode}")
    if result.returncode != 0:
        print(result.stdout[-4000:], result.stderr[-2000:])

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
