"""
Analyze MeshAgent experiment results from JSONL output files.

Features:
  Fig 9  — Abstention confusion matrix & metrics
  Paper  — Accuracy, per-difficulty, per-type, reliability
  Detail — Failed query report with error classification

Usage:
    python scripts/analyze_results.py [jsonl_path]

    If no path given, defaults to: logs/debug/baseline_static.jsonl
"""

import json
import sys
import os
import re
from collections import defaultdict
from io import StringIO


class TeeOutput:
    """Duplicate writes to both original stdout and a StringIO buffer."""
    def __init__(self, original, buffer):
        self.original = original
        self.buffer = buffer

    def write(self, data):
        self.original.write(data)
        self.buffer.write(data)

    def flush(self):
        self.original.flush()


# ── JSONL parsing ──────────────────────────────────────────────────────────

def load_jsonl(path):
    records = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def group_queries(records):
    """Group into per-query chunks. Each chunk starts with {'query': ...}."""
    queries = []
    current = None
    for rec in records:
        if "query" in rec:
            current = {"query": rec["query"], "results": []}
            queries.append(current)
        elif current is not None:
            current["results"].append(rec)
    return queries


# ── Classification ─────────────────────────────────────────────────────────

def classify_difficulty(query):
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
    if "Return a list" in query:
        return "list"
    elif "Return a table" in query:
        return "table"
    elif any(x in query for x in [
        "Return the new graph", "Return the graph",
        "Return the balanced graph", "Return the networkx graph",
        "Create the new graph",
    ]):
        return "graph"
    else:
        return "text"


def classify_error_reason(error_msg):
    """
    Classify failure reason from error message.
    Returns (category, detail) tuple.
    """
    if not error_msg:
        return ("unknown", "")

    msg = str(error_msg).lower()

    # Import / name errors
    if re.search(r"name\s+['\"]?\w+['\"]?\s+is not defined", msg):
        match = re.search(r"name\s+['\"]?(\w+)['\"]?\s+is not defined", msg)
        name = match.group(1) if match else "?"
        return ("missing_import", f"'{name}' is not defined (missing import)")

    if "no module named" in msg:
        return ("missing_import", "missing module import")
    if "cannot import" in msg:
        return ("missing_import", "import error")

    # Type errors
    if re.search(r"type\s*error", msg) or "'NoneType' object" in msg:
        return ("type_error", error_msg[:100])

    # Attribute errors
    if "has no attribute" in msg or "attributeerror" in msg:
        return ("attribute_error", error_msg[:100])

    # Key / Index errors
    if "keyerror" in msg or re.search(r"key\s*error", msg):
        return ("key_error", error_msg[:100])
    if "indexerror" in msg or "list index out of range" in msg:
        return ("index_error", error_msg[:100])

    # NetworkX errors
    if "networkx" in msg.lower() or "has_path" in msg:
        return ("graph_logic_error", error_msg[:100])

    # List/dict operation errors
    if "list.remove" in msg or "x not in list" in msg:
        return ("collection_error", error_msg[:100])
    if "cannot unpack" in msg:
        return ("unpack_error", error_msg[:100])

    # Verifier errors (from MyChecker)
    if "verify_" in msg or "check failed" in msg.lower():
        return ("verifier_error", error_msg[:100])

    # Logic / semantic
    if "division by zero" in msg or "float division" in msg:
        return ("math_error", error_msg[:100])

    return ("other", error_msg[:100])


ERROR_CATEGORIES = [
    "missing_import", "type_error", "attribute_error", "key_error",
    "index_error", "graph_logic_error", "collection_error", "unpack_error",
    "math_error", "verifier_error", "other",
]

ERROR_LABELS = {
    "missing_import":    "Missing import / undefined name",
    "type_error":        "TypeError / NoneType",
    "attribute_error":   "AttributeError",
    "key_error":         "KeyError",
    "index_error":       "IndexError",
    "graph_logic_error": "Graph logic error (NetworkX)",
    "collection_error":  "List/dict operation error",
    "unpack_error":      "Unpacking error",
    "math_error":        "Math error (div-by-zero etc.)",
    "verifier_error":    "Verifier constraint violation",
    "other":             "Other",
}


# ── Data extraction ────────────────────────────────────────────────────────

def get_result(q):
    for rec in q["results"]:
        if "Result" in rec:
            return rec["Result"]
    return "unknown"


def get_error(q):
    for rec in q["results"]:
        if "Error" in rec:
            return rec["Error"]
    return None


def get_llm_code(q):
    for rec in q["results"]:
        if "LLM code" in rec:
            return rec["LLM code"]
    return ""


def get_debug_counts(q):
    exec_count = 0
    verif_count = 0
    for rec in q["results"]:
        if "Execution debug count" in rec:
            exec_count = rec["Execution debug count"]
        if "Verifier debug count" in rec:
            verif_count = rec["Verifier debug count"]
    return exec_count, verif_count


def get_confidence_data(q):
    """Extract S_confidence, C_semantic, and abstain reason from query results."""
    s_conf = None
    c_sem = None
    reason = None
    for rec in q["results"]:
        if "S_confidence" in rec:
            s_conf = rec["S_confidence"]
        if "C_semantic" in rec:
            c_sem = rec["C_semantic"]
        if "Abstain reason" in rec:
            reason = rec["Abstain reason"]
    return s_conf, c_sem, reason


def has_verifier_failure(q):
    """Check if this query has verifier-related failure in results."""
    for rec in q["results"]:
        if "Result" in rec and "verifiers" in rec["Result"].lower():
            return True
        if "Error" in rec and "verify_" in str(rec["Error"]):
            return True
    return False


# ── Abstention analysis ────────────────────────────────────────────────────
#
#  Fig. 9 Confusion Matrix (from paper):
#
#              Correct Results    Wrong Results
#  Output        a                  b
#  Abstain       c                  d
#
#  Metrics:
#    Abstention Accuracy  = (a + d) / (a + b + c + d)
#    Abstention Precision = d / (c + d)
#    Abstention Recall    = d / (b + d)
#    Abstention Rate      = (c + d) / (a + b + c + d)
#
#  In MeshAgent: "Abstain" = verifier failure or confidence < threshold
#  For baseline (no explicit abstention):
#    "Fail, code cannot run" → treated as abstention (no valid output)
#    "Fail" (mismatch)       → wrong output (b)
#    "Pass"                  → correct output (a)
#  For CoT+error_check (with verifier):
#    Verifier failure        → abstention
#    Verifier pass + match   → a
#    Verifier pass + mismatch→ b
# ────────────────────────────────────────────────────────────────────────────

def compute_abstention(queries):
    """
    Compute Fig. 9 confusion matrix and abstention metrics.
    Returns dict with a, b, c, d and four metrics.
    """
    a = 0  # Output + Correct
    b = 0  # Output + Wrong
    c = 0  # Abstain + Would-be-correct
    d = 0  # Abstain + Would-be-wrong

    for q in queries:
        result = get_result(q)
        _, _, abstain_reason = get_confidence_data(q)

        # Explicit abstention from confidence mechanism (Stage 6)
        if result == "Abstain":
            if abstain_reason and "verifier failed" in abstain_reason:
                d += 1  # correct abstention: code wouldn't pass verifier
            else:
                c += 1  # false abstention: system was over-cautious
        elif result == "Pass":
            a += 1
        elif "code cannot run" in result.lower():
            d += 1  # passive abstention: code failed
        else:
            b += 1  # wrong output

    total = a + b + c + d
    if total == 0:
        return {"a": 0, "b": 0, "c": 0, "d": 0,
                "accuracy": 0, "precision": 0, "recall": 0, "rate": 0}

    abst_accuracy = (a + d) / total
    abst_precision = d / (c + d) if (c + d) > 0 else 0
    abst_recall = d / (b + d) if (b + d) > 0 else 0
    abst_rate = (c + d) / total

    return {
        "a": a, "b": b, "c": c, "d": d,
        "abstention_accuracy": abst_accuracy,
        "abstention_precision": abst_precision,
        "abstention_recall": abst_recall,
        "abstention_rate": abst_rate,
    }


# ── Main analysis ──────────────────────────────────────────────────────────

def analyze(path):
    records = load_jsonl(path)
    queries = group_queries(records)

    total = len(queries)
    passed = 0
    failed_run = 0
    failed_mismatch = 0
    failed_verifier = 0

    by_difficulty = defaultdict(lambda: {"total": 0, "pass": 0, "fail_run": 0, "fail_mismatch": 0, "fail_verifier": 0, "abstain": 0})
    by_type = defaultdict(lambda: {"total": 0, "pass": 0, "fail_run": 0, "fail_mismatch": 0, "fail_verifier": 0, "abstain": 0})
    error_categories = defaultdict(int)
    debug_stats = {"total_exec": 0, "total_verif": 0, "max_exec": 0, "max_verif": 0, "queries_with_debug": 0}
    confidence_stats = {"total": 0, "sum": 0.0, "min": 1.0, "max": 0.0, "abstain_count": 0}

    failed_queries = []

    for q in queries:
        difficulty = classify_difficulty(q["query"])
        rtype = classify_return_type(q["query"])
        result = get_result(q)
        error = get_error(q)
        code = get_llm_code(q)
        exec_debug, verif_debug = get_debug_counts(q)
        code_lines = code.count("\n") + 1 if code else 0

        # Track debug stats
        if exec_debug > 0 or verif_debug > 0:
            debug_stats["queries_with_debug"] += 1
            debug_stats["total_exec"] += exec_debug
            debug_stats["total_verif"] += verif_debug
            debug_stats["max_exec"] = max(debug_stats["max_exec"], exec_debug)
            debug_stats["max_verif"] = max(debug_stats["max_verif"], verif_debug)

        # Track confidence scores
        s_conf, c_sem, abstain_reason = get_confidence_data(q)
        if s_conf is not None:
            confidence_stats["total"] += 1
            confidence_stats["sum"] += s_conf
            confidence_stats["min"] = min(confidence_stats["min"], s_conf)
            confidence_stats["max"] = max(confidence_stats["max"], s_conf)
        if abstain_reason:
            confidence_stats["abstain_count"] += 1

        by_difficulty[difficulty]["total"] += 1
        by_type[rtype]["total"] += 1

        if result == "Pass":
            passed += 1
            by_difficulty[difficulty]["pass"] += 1
            by_type[rtype]["pass"] += 1
        elif result == "Abstain":
            s_conf, c_sem, abstain_reason = get_confidence_data(q)
            by_difficulty[difficulty]["abstain"] += 1
            by_type[rtype]["abstain"] += 1
            failed_queries.append({
                "query": q["query"],
                "difficulty": difficulty,
                "type": rtype,
                "reason": f"abstain ({abstain_reason or 'unknown'})",
                "code_lines": code_lines,
            })
        elif "verifiers" in result.lower() and "fail" in result.lower():
            failed_verifier += 1
            by_difficulty[difficulty]["fail_verifier"] += 1
            by_type[rtype]["fail_verifier"] += 1
            failed_queries.append({
                "query": q["query"],
                "difficulty": difficulty,
                "type": rtype,
                "reason": "verifier failure (abstention)",
                "error": error,
                "code_lines": code_lines,
            })
        elif "code cannot run" in result.lower():
            failed_run += 1
            by_difficulty[difficulty]["fail_run"] += 1
            by_type[rtype]["fail_run"] += 1
            cat, detail = classify_error_reason(error)
            error_categories[cat] += 1
            failed_queries.append({
                "query": q["query"],
                "difficulty": difficulty,
                "type": rtype,
                "reason": "code cannot run",
                "error_category": cat,
                "error_detail": detail,
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

    # Abstention analysis
    abst = compute_abstention(queries)

    # ═══════════════════════════════════════════════════════════════════════
    #  Report
    # ═══════════════════════════════════════════════════════════════════════

    print("=" * 72)
    print(f"  MeshAgent Experiment Analysis: {os.path.basename(path)}")
    print("=" * 72)
    print()
    print(f"  Total queries:        {total}")
    print(f"  Passed:               {passed}  ({passed/total*100:.1f}%)")
    print(f"  Failed (run error):   {failed_run}  ({failed_run/total*100:.1f}%)")
    print(f"  Failed (mismatch):    {failed_mismatch}  ({failed_mismatch/total*100:.1f}%)")
    if failed_verifier > 0:
        print(f"  Failed (verifier):    {failed_verifier}  ({failed_verifier/total*100:.1f}%)")
    print()

    # ── By Difficulty ──
    print("-" * 56)
    print(f"  By Difficulty")
    header = f"  {'':<12} {'Total':>6} {'Pass':>6} {'Pass%':>7} {'RunErr':>6} {'MisMat':>6}"
    if failed_verifier > 0:
        header += f" {'VfyFail':>7}"
    print(header)
    print("-" * 56)
    for level in ["easy", "medium", "hard"]:
        d = by_difficulty[level]
        if d["total"] == 0:
            continue
        pct = d["pass"] / d["total"] * 100
        row = f"  {level:<12} {d['total']:>6} {d['pass']:>6} {pct:>6.1f}% {d['fail_run']:>6} {d['fail_mismatch']:>6}"
        if failed_verifier > 0:
            row += f" {d['fail_verifier']:>7}"
        print(row)

    # ── By Return Type ──
    print()
    print("-" * 56)
    print(f"  By Return Type")
    header2 = f"  {'':<12} {'Total':>6} {'Pass':>6} {'Pass%':>7} {'RunErr':>6} {'MisMat':>6}"
    if failed_verifier > 0:
        header2 += f" {'VfyFail':>7}"
    print(header2)
    print("-" * 56)
    for rtype in ["text", "list", "table", "graph"]:
        d = by_type[rtype]
        if d["total"] == 0:
            continue
        pct = d["pass"] / d["total"] * 100
        row = f"  {rtype:<12} {d['total']:>6} {d['pass']:>6} {pct:>6.1f}% {d['fail_run']:>6} {d['fail_mismatch']:>6}"
        if failed_verifier > 0:
            row += f" {d['fail_verifier']:>7}"
        print(row)

    # ── FIG 9: Abstention Confusion Matrix ──
    print()
    print("=" * 72)
    print("  Fig. 9 — Abstention Confusion Matrix")
    print("=" * 72)
    print(f"""
                    Correct Results    Wrong Results
    Output              a = {abst['a']:<4}            b = {abst['b']:<4}
    Abstain             c = {abst['c']:<4}            d = {abst['d']:<4}
""")
    print(f"  Abstention Accuracy:   {abst['abstention_accuracy']*100:.1f}%  ← (a+d)/(a+b+c+d)")
    print(f"  Abstention Precision:  {abst['abstention_precision']*100:.1f}%  ← d/(c+d)")
    print(f"  Abstention Recall:     {abst['abstention_recall']*100:.1f}%  ← d/(b+d)")
    print(f"  Abstention Rate:       {abst['abstention_rate']*100:.1f}%  ← (c+d)/(a+b+c+d)")
    print()

    # ── Failure Reason Classification ──
    if error_categories:
        print("=" * 72)
        print("  Failure Reason Classification")
        print("=" * 72)
        print(f"  {'Category':<35} {'Count':>6}")
        print("  " + "-" * 42)
        for cat in ERROR_CATEGORIES:
            count = error_categories.get(cat, 0)
            if count > 0:
                label = ERROR_LABELS.get(cat, cat)
                print(f"  {label:<35} {count:>6}")
        print()

    # ── Failed Queries Detail ──
    if failed_queries:
        print("=" * 72)
        print(f"  Failed Queries Detail ({len(failed_queries)} queries)")
        print("=" * 72)
        for i, fq in enumerate(failed_queries, 1):
            qs = fq["query"][:75] + "..." if len(fq["query"]) > 75 else fq["query"]
            print(f"  [{i:>2}] [{fq['difficulty']}][{fq['type']}] {fq['reason']}")
            print(f"       {qs}")
            if fq.get("error_category"):
                label = ERROR_LABELS.get(fq["error_category"], fq["error_category"])
                print(f"       Category: {label}")
                print(f"       Detail:   {fq['error_detail']}")
            elif fq.get("error"):
                err_short = str(fq["error"])[:120]
                print(f"       Error: {err_short}")
            if fq.get("code_lines"):
                print(f"       Code lines: {fq['code_lines']}")
            print()

    # ── Paper Metrics Summary ──
    print("=" * 72)
    print("  Paper-Relevant Metrics Summary")
    print("=" * 72)
    print(f"  Accuracy (overall):              {passed/total*100:.1f}%")
    print(f"  Accuracy (easy):                 {by_difficulty['easy']['pass']/max(by_difficulty['easy']['total'],1)*100:.1f}%")
    print(f"  Accuracy (medium):               {by_difficulty['medium']['pass']/max(by_difficulty['medium']['total'],1)*100:.1f}%")
    print(f"  Accuracy (hard):                 {by_difficulty['hard']['pass']/max(by_difficulty['hard']['total'],1)*100:.1f}%")
    print(f"  Reliability (code exec rate):    {(total - failed_run)/total*100:.1f}%")
    print(f"  Abstention Accuracy:             {abst['abstention_accuracy']*100:.1f}%")
    print(f"  Abstention Precision:            {abst['abstention_precision']*100:.1f}%")
    print(f"  Abstention Recall:               {abst['abstention_recall']*100:.1f}%")
    print(f"  Abstention Rate:                 {abst['abstention_rate']*100:.1f}%")
    if debug_stats["queries_with_debug"] > 0:
        avg_exec = debug_stats["total_exec"] / max(debug_stats["queries_with_debug"], 1)
        avg_verif = debug_stats["total_verif"] / max(debug_stats["queries_with_debug"], 1)
        print(f"  Avg Execution Debug Iters:       {avg_exec:.1f}")
        print(f"  Avg Verifier Debug Iters:        {avg_verif:.1f}")
        print(f"  Max Execution Debug Iters:       {debug_stats['max_exec']}")
        print(f"  Max Verifier Debug Iters:        {debug_stats['max_verif']}")
        print(f"  Queries with Debug:              {debug_stats['queries_with_debug']}/{total}")
    if confidence_stats["total"] > 0:
        avg_s = confidence_stats["sum"] / confidence_stats["total"]
        print(f"  Avg S_confidence:                {avg_s:.3f}")
        print(f"  S_confidence Range:              [{confidence_stats['min']:.3f}, {confidence_stats['max']:.3f}]")
        print(f"  Explicit Abstentions:            {confidence_stats['abstain_count']}/{total}")
    print()

    return {
        "total": total,
        "passed": passed,
        "failed_run": failed_run,
        "failed_mismatch": failed_mismatch,
        "failed_verifier": failed_verifier,
        "accuracy": passed / total * 100,
        "abstention": abst,
        "error_categories": dict(error_categories),
        "debug_stats": debug_stats,
        "confidence_stats": confidence_stats,
        "by_difficulty": {k: dict(v) for k, v in by_difficulty.items()},
        "by_type": {k: dict(v) for k, v in by_type.items()},
    }


# ── Export cleaned data ─────────────────────────────────────────────────────

def export_results(results, queries, path, output_dir, base_output_dir):
    os.makedirs(output_dir, exist_ok=True)

    basename = os.path.splitext(os.path.basename(path))[0]

    # 1. Summary JSON
    summary = {
        "experiment": basename,
        "total_queries": results["total"],
        "passed": results["passed"],
        "failed_run": results["failed_run"],
        "failed_mismatch": results["failed_mismatch"],
        "failed_verifier": results["failed_verifier"],
        "accuracy": round(results["accuracy"], 2),
        "reliability": round((results["total"] - results["failed_run"]) / results["total"] * 100, 2),
        "abstention": {
            "matrix": {"a": results["abstention"]["a"],
                       "b": results["abstention"]["b"],
                       "c": results["abstention"]["c"],
                       "d": results["abstention"]["d"]},
            "accuracy": round(results["abstention"]["abstention_accuracy"] * 100, 2),
            "precision": round(results["abstention"]["abstention_precision"] * 100, 2),
            "recall": round(results["abstention"]["abstention_recall"] * 100, 2),
            "rate": round(results["abstention"]["abstention_rate"] * 100, 2),
        },
        "by_difficulty": results["by_difficulty"],
        "by_type": results["by_type"],
        "error_categories": results["error_categories"],
        "debug_stats": results["debug_stats"],
        "confidence_stats": results["confidence_stats"],
    }
    summary_path = os.path.join(output_dir, f"{basename}_summary.json")
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"  Summary exported → {output_dir}/{os.path.basename(summary_path)}")

    # 2. Per-query JSON — one record per query, flattened
    query_records = []
    for q in queries:
        result_str = get_result(q)
        error = get_error(q)
        code = get_llm_code(q)

        is_pass = result_str == "Pass"
        is_run_error = "code cannot run" in result_str.lower()
        is_verifier_fail = "verifiers" in result_str.lower() and "fail" in result_str.lower()

        cat, detail = "", ""
        if error and is_run_error:
            cat, detail = classify_error_reason(error)

        rec = {
            "query": q["query"][:120],
            "difficulty": classify_difficulty(q["query"]),
            "return_type": classify_return_type(q["query"]),
            "result": result_str,
            "passed": is_pass,
            "is_run_error": is_run_error,
            "is_verifier_failure": is_verifier_fail,
            "error_category": cat,
            "error_detail": detail,
            "code_lines": code.count("\n") + 1 if code else 0,
        }
        exec_db, verif_db = get_debug_counts(q)
        s_conf, c_sem, abstain_reason = get_confidence_data(q)
        if exec_db > 0 or verif_db > 0:
            rec["execution_debug_count"] = exec_db
            rec["verifier_debug_count"] = verif_db
        if s_conf is not None:
            rec["S_confidence"] = s_conf
            rec["C_semantic"] = c_sem
            rec["abstain_reason"] = abstain_reason
        query_records.append(rec)

    queries_path = os.path.join(output_dir, f"{basename}_queries.json")
    with open(queries_path, "w") as f:
        json.dump(query_records, f, indent=2, ensure_ascii=False)
        print(f"  Queries exported  → {output_dir}/{os.path.basename(queries_path)}")

    # 3. Cross-experiment comparison CSV (append mode, at results/ level)
    csv_path = os.path.join(base_output_dir, "all_experiments.csv")
    is_new_file = not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0
    with open(csv_path, "a") as f:
        if is_new_file:
            f.write("experiment,total,pass,accuracy,reliability,"
                    "abst_accuracy,abst_precision,abst_recall,abst_rate,"
                    "easy_acc,medium_acc,hard_acc,"
                    "text_acc,list_acc,table_acc,graph_acc,"
                    "fail_run,fail_mismatch,"
                    "avg_exec_debug,avg_verif_debug,queries_with_debug,"
                    "avg_S_confidence,abstain_count\n")
        easy_acc = results["by_difficulty"]["easy"]["pass"] / max(results["by_difficulty"]["easy"]["total"], 1) * 100
        med_acc = results["by_difficulty"]["medium"]["pass"] / max(results["by_difficulty"]["medium"]["total"], 1) * 100
        hard_acc = results["by_difficulty"]["hard"]["pass"] / max(results["by_difficulty"]["hard"]["total"], 1) * 100
        rel = (results["total"] - results["failed_run"]) / results["total"] * 100

        def type_acc(t):
            d = results["by_type"].get(t, {"pass": 0, "total": 0})
            return d["pass"] / max(d["total"], 1) * 100

        f.write(f"{basename},{results['total']},{results['passed']},"
                f"{results['accuracy']:.1f},{rel:.1f},"
                f"{results['abstention']['abstention_accuracy']*100:.1f},"
                f"{results['abstention']['abstention_precision']*100:.1f},"
                f"{results['abstention']['abstention_recall']*100:.1f},"
                f"{results['abstention']['abstention_rate']*100:.1f},"
                f"{easy_acc:.1f},{med_acc:.1f},{hard_acc:.1f},"
                f"{type_acc('text'):.1f},{type_acc('list'):.1f},"
                f"{type_acc('table'):.1f},{type_acc('graph'):.1f},"
                f"{results['failed_run']},{results['failed_mismatch']},"
                f"{results['debug_stats']['total_exec']/max(results['debug_stats']['queries_with_debug'],1):.1f},"
                f"{results['debug_stats']['total_verif']/max(results['debug_stats']['queries_with_debug'],1):.1f},"
                f"{results['debug_stats']['queries_with_debug']},"
                f"{results['confidence_stats']['sum']/max(results['confidence_stats']['total'],1):.3f},"
                f"{results['confidence_stats']['abstain_count']}\n")
        print(f"  Comparison CSV    → {base_output_dir}/{os.path.basename(csv_path)}")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "logs/debug/baseline_static.jsonl"
    if not os.path.exists(path):
        print(f"Error: file not found: {path}")
        sys.exit(1)

    experiment_name = os.path.splitext(os.path.basename(path))[0]

    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.join(script_dir, "..", "..")
    base_output_dir = os.path.join(project_root, "results")

    exp_output_dir = os.path.join(base_output_dir, experiment_name)

    records = load_jsonl(path)
    queries = group_queries(records)

    captured = StringIO()
    old_stdout = sys.stdout
    sys.stdout = TeeOutput(old_stdout, captured)

    results = analyze(path)

    sys.stdout = old_stdout

    export_results(results, queries, path, exp_output_dir, base_output_dir)

    report_path = os.path.join(exp_output_dir, f"{experiment_name}.txt")
    os.makedirs(exp_output_dir, exist_ok=True)
    with open(report_path, "w") as f:
        f.write(captured.getvalue())
    print(f"  Report saved      → {exp_output_dir}/{experiment_name}.txt")
