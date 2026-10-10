#!/usr/bin/env python3
"""Check the DQ results against the generator's answer key (data/planted_issues.json).

For every rule, the expected failure count is the sum over the planted issues that
rule should catch; rules with no planted issue should report 0. Compares that with
the latest result for each rule in dq.result.

This is the test that proves the controls work: every planted issue is detected,
and nothing clean is flagged.

Usage:  python pipeline/verify_planted.py
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict

from db import DATA_DIR, connect


def main() -> None:
    key_path = DATA_DIR / "planted_issues.json"
    issues = json.loads(key_path.read_text(encoding="utf-8"))["issues"]

    expected: dict[str, int] = defaultdict(int)
    issues_by_rule: dict[str, list[str]] = defaultdict(list)
    for issue in issues:
        for check in issue["checks"]:
            expected[check["rule_id"]] += check["expected_failures"]
            issues_by_rule[check["rule_id"]].append(issue["issue_id"])

    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT c.rule_id, c.rule_name, s.rows_failed
        FROM dq.check_rule c
        LEFT JOIN dq.vw_latest_rule_status s ON s.rule_id = c.rule_id
        WHERE c.is_active = 1
        ORDER BY c.rule_id""")
    actual = {r.rule_id: (r.rule_name, r.rows_failed) for r in cur.fetchall()}
    conn.close()

    if all(v[1] is None for v in actual.values()):
        sys.exit("No DQ results yet. Run the pipeline first (make run).")

    print(f"{'rule':<9}{'expected':>9}{'actual':>8}  {'result':<8}{'planted issues':<16}rule")
    rule_ok: dict[str, bool] = {}
    for rule_id, (name, failed) in actual.items():
        exp = expected.get(rule_id, 0)
        ok = failed == exp
        rule_ok[rule_id] = ok
        result = "OK" if ok else ("NOT RUN" if failed is None else "MISMATCH")
        print(f"{rule_id:<9}{exp:>9}{'-' if failed is None else failed:>8}  {result:<8}"
              f"{', '.join(sorted(set(issues_by_rule.get(rule_id, [])))) or '-':<16}{name}")

    detected = [i["issue_id"] for i in issues if all(rule_ok.get(c["rule_id"], False) for c in i["checks"])]
    missed = [i["issue_id"] for i in issues if i["issue_id"] not in detected]
    extra = [r for r, ok in rule_ok.items() if not ok and r not in issues_by_rule]

    print(f"\nPlanted issues detected exactly: {len(detected)}/{len(issues)}")
    if missed:
        print(f"Not matched: {', '.join(missed)}")
    if extra:
        print(f"Rules failing with no planted issue (unexpected): {', '.join(extra)}")
    if missed or extra:
        print("Note: after the RCA fix (crop aliases), H01 is expected to stop matching. That is the fix working.")
        sys.exit(1)


if __name__ == "__main__":
    main()
