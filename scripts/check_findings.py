from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from can_diag.scenario import scenario_by_name


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compare the classifications in a diagnostic report with the findings the scenario "
            "ground truth says its injected conditions should produce."
        )
    )
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--scenario", required=True)
    arguments = parser.parse_args(argv)

    try:
        scenario = scenario_by_name(arguments.scenario)
    except KeyError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    if not arguments.report.is_file():
        print(f"error: report not found: {arguments.report.name}", file=sys.stderr)
        return 1

    payload = json.loads(arguments.report.read_text(encoding="utf-8"))
    observed = set(payload.get("event_counts", {}))
    expected: set[str] = set()
    for injection in scenario.injections:
        expected.update(injection.expected_findings)

    missing = sorted(expected - observed)
    unexpected = sorted(observed - expected)

    print(f"scenario: {scenario.name}")
    print(f"expected findings: {', '.join(sorted(expected)) or 'none'}")
    print(f"observed findings: {', '.join(sorted(observed)) or 'none'}")

    if missing:
        print(f"error: expected findings never raised: {', '.join(missing)}", file=sys.stderr)
    if unexpected:
        print(f"error: unexpected findings raised: {', '.join(unexpected)}", file=sys.stderr)
    if missing or unexpected:
        return 1

    for node in {injection.node for injection in scenario.injections}:
        nodes = {event["node"] for event in payload.get("events", [])}
        if node not in nodes:
            print(f"error: no finding was attributed to {node}", file=sys.stderr)
            return 1

    print("report findings match the scenario ground truth")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
