"""
Analyze MeshAgent experiment results from JSONL output files.

Usage:
    python scripts/analyze_results.py [jsonl_path]

    If no path given, defaults to: logs/debug/baseline_static.jsonl

Produces a summary table with:
  - Overall accuracy (pass rate)
  - Breakdown by query difficulty (easy / medium / hard)
  - Breakdown by return type (text / list / table / graph)
  - Failure mode breakdown (run error vs result mismatch)
  - Per-query detail table
"""

import json
import sys
import os
from collections import defaultdict


def load_jsonl(path):
    """Load JSONL file into a list of dicts, grouped by query."""
    records = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def group_queries(records):
    """Group records into per-query chunks. Each chunk starts with {'query': ...}."""
    queries = []
    current = None
    for rec in records:
        if "query" in rec:
            current = {"query": rec["query"], "results": []}
            queries.append(current)
        elif current is not None:
            current["results"].append(rec)
    return queries


def classify_difficulty(query):
    """Classify query difficulty based on paper's categorization."""
    easy_keywords = [
        "List all ports contained in packet switch",
        "Add a new packet_switch",
        "Update the physical_capacity_bps",
        "Identify all CONTROL_POINT",
        "Display all CONTROL_DOMAIN",
        "Update all PACKET_SWITCH with node attr",
        "Find the number of CHASSIS",
    ]
    medium_keywords = [
        "What is the bandwidth on packet switch",
        "What is the bandwidth on each AGG_BLOCK",
        "Find the first and the second largest Chassis",
        "Show the average physical_capacity_bps",
        "For each AGG_BLOCK, list the number",
        "Identify all PACKET_SWITCH nodes contains in AGG_BLOCK",
        "Find all PACKET_SWITCH nodes that have capacity more than the average",
    ]
    for kw in easy_keywords:
        if query.startswith(kw):
            return "easy"
    for kw in medium_keywords:
        if query.startswith(kw):
            return "medium"
    return "hard"


def classify_return_type(query):
    """Infer expected return type from query."""
    if "Return a list" in query:
        return "list"
    elif "Return a table" in query:
        return "table"
    elif "Return the new graph" in query or "Return the graph" in query or \
         "Return the balanced graph" in query or "Return the networkx graph" in query or \
         "Create the new graph" in query:
        return "graph"
    else:
        return "text"


def get_result(q):
    """Extract the Result string from query's results."""
    for rec in q["results"]:
        if "Result" in rec:
            return rec["Result"]
    return "unknown"


def get_error(q):
    """Extract error message if present."""
    for rec in q["results"]:
        if "Error" in rec:
            return rec["Error"]
    return None


def get_token_count(q):
    """Extract token count if present."""
    for rec in q["results"]:
        if "LLM output token count" in rec:
            return rec["LLM output token count"]
    return 0


def get_llm_code(q):
    """Extract LLM-generated code."""
    for rec in q["results"]:
        if "LLM code" in rec:
            return rec["LLM code"]
    return ""


def analyze(path):
    records = load_jsonl(path)
    queries = group_queries(records)

    # Stats containers
    total = len(queries)
    passed = 0
    failed_run = 0
    failed_mismatch = 0

    by_difficulty = defaultdict(lambda: {"total": 0, "pass": 0, "fail_run": 0, "fail_mismatch": 0})
    by_type = defaultdict(lambda: {"total": 0, "pass": 0, "fail_run": 0, "fail_mismatch": 0})

    failed_queries = []

    for q in queries:
        difficulty = classify_difficulty(q["query"])
        rtype = classify_return_type(q["query"])
        result = get_result(q)
        error = get_error(q)
        code = get_llm_code(q)
        code_lines = code.count("\n") + 1 if code else 0

        by_difficulty[difficulty]["total"] += 1
        by_type[rtype]["total"] += 1

        if result == "Pass":
            passed += 1
            by_difficulty[difficulty]["pass"] += 1
            by_type[rtype]["pass"] += 1
        elif "Fail, code cannot run" in result:
            failed_run += 1
            by_difficulty[difficulty]["fail_run"] += 1
            by_type[rtype]["fail_run"] += 1
            failed_queries.append({
                "query": q["query"],
                "difficulty": difficulty,
                "type": rtype,
                "reason": "code cannot run",
                "error": error,
                "code_lines": code_lines,
            })
        else:
            failed_mismatch += 1
            by_difficulty[difficulty]["fail_mismatch"] += 1
            by_type[rtype]["fail_mismatch"] += 1
            failed_queries.append({
                "query": q["query"],
                "difficulty": difficulty,
                "type": rtype,
                "reason": "result mismatch",
                "code_lines": code_lines,
            })

    # Print summary
    print("=" * 70)
    print(f"  MeshAgent Experiment Analysis: {os.path.basename(path)}")
    print("=" * 70)
    print()
    print(f"  Total queries:        {total}")
    print(f"  Passed:               {passed}  ({passed/total*100:.1f}%)")
    print(f"  Failed (run error):   {failed_run}  ({failed_run/total*100:.1f}%)")
    print(f"  Failed (mismatch):    {failed_mismatch}  ({failed_mismatch/total*100:.1f}%)")
    print()

    # By difficulty
    print("-" * 50)
    print(f"  By Difficulty")
    print(f"  {'':<12} {'Total':>6} {'Pass':>6} {'Pass%':>7} {'RunErr':>6} {'MisMatch':>8}")
    print("-" * 50)
    for level in ["easy", "medium", "hard"]:
        d = by_difficulty[level]
        if d["total"] == 0:
            continue
        pct = d["pass"] / d["total"] * 100
        print(f"  {level:<12} {d['total']:>6} {d['pass']:>6} {pct:>6.1f}% {d['fail_run']:>6} {d['fail_mismatch']:>8}")

    # By return type
    print()
    print("-" * 50)
    print(f"  By Return Type")
    print(f"  {'':<12} {'Total':>6} {'Pass':>6} {'Pass%':>7} {'RunErr':>6} {'MisMatch':>8}")
    print("-" * 50)
    for rtype in ["text", "list", "table", "graph"]:
        d = by_type[rtype]
        if d["total"] == 0:
            continue
        pct = d["pass"] / d["total"] * 100
        print(f"  {rtype:<12} {d['total']:>6} {d['pass']:>6} {pct:>6.1f}% {d['fail_run']:>6} {d['fail_mismatch']:>8}")

    # Failed queries detail
    if failed_queries:
        print()
        print("=" * 70)
        print(f"  Failed Queries Detail ({len(failed_queries)} queries)")
        print("=" * 70)
        for i, fq in enumerate(failed_queries, 1):
            query_short = fq["query"][:80] + "..." if len(fq["query"]) > 80 else fq["query"]
            print(f"  [{i}] [{fq['difficulty']}][{fq['type']}] {fq['reason']}")
            print(f"      {query_short}")
            if fq.get("error"):
                err_short = str(fq["error"])[:100]
                print(f"      Error: {err_short}")
            print(f"      Code lines: {fq['code_lines']}")
            print()

    # Paper-relevant metrics
    print("=" * 70)
    print("  Paper-Relevant Metrics")
    print("=" * 70)
    print(f"  Accuracy (overall):            {passed/total*100:.1f}%")
    print(f"  Accuracy (easy only):          {by_difficulty['easy']['pass']/max(by_difficulty['easy']['total'],1)*100:.1f}%")
    print(f"  Accuracy (medium only):        {by_difficulty['medium']['pass']/max(by_difficulty['medium']['total'],1)*100:.1f}%")
    print(f"  Accuracy (hard only):          {by_difficulty['hard']['pass']/max(by_difficulty['hard']['total'],1)*100:.1f}%")
    print(f"  Reliability (code exec rate):  {(total - failed_run)/total*100:.1f}%")
    print(f"                                          (queries where code successfully ran)")
    print()

    # Return stats dict for programmatic use
    return {
        "total": total,
        "passed": passed,
        "failed_run": failed_run,
        "failed_mismatch": failed_mismatch,
        "accuracy": passed / total * 100,
        "by_difficulty": {k: dict(v) for k, v in by_difficulty.items()},
        "by_type": {k: dict(v) for k, v in by_type.items()},
    }


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "logs/debug/baseline_static.jsonl"
    if not os.path.exists(path):
        print(f"Error: file not found: {path}")
        sys.exit(1)
    analyze(path)
