"""
MeshAgent Stage 6: Full pipeline with confidence-based abstention.

Combines all prior stages (query-specific constraints, CoT, verifier, tools)
and adds the paper's heuristic confidence scoring mechanism:
  C_semantic = avg pairwise cosine similarity of outputs across N runs
  S_confidence = w * C_semantic + (1-w) * (1 - I_debug / N_max)
  if S_confidence < 0.7 → abstain

Requires EACH_PROMPT_RUN_TIME >= 3 to compute semantic consistency.
"""

import json, traceback, os, sys, time, re, copy as cp
import numpy as np
import networkx as nx
import jsonlines
from collections import Counter
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from copy_ai_models_cot import summary_gen_chain, cot_plus_tool_chain, pySelfDebugger
from helper import (
    getGraphData, generate_embeddings, clean_up_llm_output_func,
    check_list_equal, node_attributes_are_equal, clean_up_output_graph_data,
)
from rag_local import init_rag, rag_constraint_search, rag_tool_search
from error_check import MyChecker
from networkx.readwrite import json_graph
from langchain.callbacks import get_openai_callback

# ── Config ─────────────────────────────────────────────────────────────────

EACH_PROMPT_RUN_TIME = 3          # >= 3 required for C_semantic
OUTPUT_JSONL_PATH = "logs/debug/full_meshagent_abstention.jsonl"
DEBUG_LOOP_TOTAL = 5              # N_max
MODEL_SOURCE = "OPENAI"
CONFIDENCE_W = 0.5                # weight of semantic consistency
CONFIDENCE_THRESHOLD = 0.7        # abstain below this

# ── Confidence Scoring ─────────────────────────────────────────────────────

def cosine_similarity(a, b):
    a, b = np.array(a), np.array(b)
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-10)


def compute_C_semantic(outputs):
    """Compute semantic consistency across multiple runs.
    Embeds each output text, computes pairwise cosine similarities, averages them.
    Returns (C_semantic, list of embeddings).
    """
    if len(outputs) < 2:
        return 0.0, []
    embeddings = [generate_embeddings(str(o)) for o in outputs if o is not None]
    if len(embeddings) < 2:
        return 0.0, embeddings
    sims = []
    for i in range(len(embeddings)):
        for j in range(i + 1, len(embeddings)):
            sims.append(cosine_similarity(embeddings[i], embeddings[j]))
    return float(np.mean(sims)), embeddings


def compute_S_confidence(verifier_passed, execution_debug_count, verifier_debug_count, C_semantic):
    """Compute confidence score per paper Eq. (2).
    Returns (S_confidence, abstain_reason).
    """
    if not verifier_passed:
        return 0.0, "verifier failed"
    I_debug = execution_debug_count + verifier_debug_count
    N = max(DEBUG_LOOP_TOTAL, 1)
    S = CONFIDENCE_W * C_semantic + (1 - CONFIDENCE_W) * (1 - I_debug / N)
    S = max(0.0, min(1.0, S))
    if S < CONFIDENCE_THRESHOLD:
        return S, "low confidence"
    return S, "confident"


# ── Self-Debug ─────────────────────────────────────────────────────────────

def self_debug_process_loop(requestData, constraints_found, code, error_details,
                            debug_status_msg, loop_time_index):
    print(debug_status_msg)
    self_debug_answer = pySelfDebugger.run({
        'input': requestData['query'],
        'constraints': constraints_found,
        'code': code,
        'error': error_details,
    })
    debugged_code = clean_up_llm_output_func(self_debug_answer)
    print("Debugged code for time:", loop_time_index)
    print(debugged_code)
    return debugged_code


def error_reduce_verify(constraints_found, requestData, code, ret_graph=None, ret_list=None):
    verifier_self_debug_count = 0
    print("================= Start verifying invariant constraints =================")
    verifier = MyChecker(ret_graph, ret_list)
    verifier_results, verifier_error = verifier.evaluate_all()
    if verifier_results:
        print("================= Congrats, verifiers all passed! =================")
    else:
        print("================= Start self-debugging for verifier errors =================")
        verifier_constraints_rag = rag_constraint_search(generate_embeddings(str(verifier_error)), top_k=2)
        debug_constraints = constraints_found + verifier_constraints_rag
        print("Verifier RAG extract constraints:", verifier_constraints_rag)
        for i in range(DEBUG_LOOP_TOTAL):
            verifier_self_debug_count += 1
            debugged_code = self_debug_process_loop(
                requestData, debug_constraints, code, verifier_error,
                debug_status_msg="================= Verifier: start self-debugging =================",
                loop_time_index=i,
            )
            try:
                _, G = getGraphData()
                exec(debugged_code)
                ret = eval("process_graph(G)")
                ret_graph_copy = clean_up_output_graph_data(ret)
                verifier = MyChecker(ret_graph_copy, ret_list)
                verifier_results, verifier_error = verifier.evaluate_all()
                if verifier_results:
                    print("================= Congrats, verifiers all passed after self-debugging! =================")
                    return verifier_results, debugged_code, verifier_self_debug_count
                else:
                    verifier_constraints_rag = rag_constraint_search(generate_embeddings(str(verifier_error)), top_k=2)
                    debug_constraints = constraints_found + verifier_constraints_rag
                    print("Verifier RAG extract constraints:", verifier_constraints_rag)
            except Exception as e:
                print(e)
                print("Fail, verifier debugged code cannot run.")
    return verifier_results, None, verifier_self_debug_count


def self_debug_execution_error(code, requestData, constraints_found):
    execution_error_self_debug = 0
    exc_type, ex, tb = sys.exc_info()
    imported_tb_info = traceback.extract_tb(tb)[-1]
    line_number = imported_tb_info[1]
    print_format = '{}: Exception in line: {}. Message: {}'
    error_details = print_format.format(exc_type.__name__, line_number, ex)
    print("Fail due to errors:", error_details)
    for i in range(DEBUG_LOOP_TOTAL):
        execution_error_self_debug += 1
        debugged_code = self_debug_process_loop(
            requestData, constraints_found, code, error_details,
            debug_status_msg="================= Error reduce: start self-debugging =================",
            loop_time_index=i,
        )
        try:
            _, G = getGraphData()
            exec(debugged_code)
            ret = eval("process_graph(G)")
            return debugged_code, ret, execution_error_self_debug
        except Exception as e:
            exc_type, ex, tb = sys.exc_info()
            imported_tb_info = traceback.extract_tb(tb)[-1]
            error_details = print_format.format(exc_type.__name__, imported_tb_info[1], ex)
            print("Fail due to errors:", error_details)
    return None, None, execution_error_self_debug


# ── Output Parsing ─────────────────────────────────────────────────────────

def diff_model_source_output_format(model_output):
    if MODEL_SOURCE == "OPENAI":
        return model_output.to_json()['kwargs']['content']
    if MODEL_SOURCE == "GOOGLE":
        return model_output
    return model_output


# ── Ground Truth Check Helpers ─────────────────────────────────────────────

def ground_truth_check_debug(requestData, ground_truth_ret, ret, llm_output_token_count,
                              execution_debug_count, verifier_debug_count):
    print("Fail the test, and here is more info:")
    if ground_truth_ret['type'] == 'graph':
        print("Two graph are not identical.")
    else:
        print("ground truth:", ground_truth_ret['data'])
        print("model output:", ret['data'])
    with jsonlines.open(OUTPUT_JSONL_PATH, mode='a') as writer:
        writer.write(requestData)
        writer.write({"Result": "Fail (wrong answer)"})
        writer.write({"Ground truth code": ground_truth_ret['reply']})
        writer.write({"LLM code": ret['reply']})
        writer.write({"Execution debug count": execution_debug_count})
        writer.write({"Verifier debug count": verifier_debug_count})
        if ground_truth_ret['type'] != 'graph':
            writer.write({"Ground truth exec": ground_truth_ret['data']})
            writer.write({"LLM code exec": ret['data']})
    return None


def ground_truth_check_accu(count, requestData, ground_truth_ret, ret, llm_output_token_count,
                             execution_debug_count, verifier_debug_count):
    print("Pass the test!")
    count += 1
    with jsonlines.open(OUTPUT_JSONL_PATH, mode='a') as writer:
        writer.write(requestData)
        writer.write({"Result": "Pass"})
        writer.write({"Ground truth code": ground_truth_ret['reply']})
        writer.write({"LLM code": ret['reply']})
        writer.write({"Execution debug count": execution_debug_count})
        writer.write({"Verifier debug count": verifier_debug_count})
        if ground_truth_ret['type'] != 'graph':
            writer.write({"Ground truth exec": ground_truth_ret['data']})
            writer.write({"LLM code exec": ret['data']})
    return count


# ── Single Query Run ───────────────────────────────────────────────────────

def run_single_query(each_prompt, constraints_found, tool_found, allAnswer):
    """Run a single iteration of the full MeshAgent pipeline for one query.
    Returns dict with code, ret, debug counts, verifier status.
    """
    requestData = {'query': each_prompt}
    _, G = getGraphData()

    summary_output = summary_gen_chain.invoke({"input": each_prompt})
    step_summary = diff_model_source_output_format(summary_output)
    steps = re.split(r'Step \d+: ', step_summary)
    steps = steps[1:]

    first_step_execution_debug_count = 0
    first_step_verifier_debug_count = 0

    first_step_llm = cot_plus_tool_chain.invoke({
        "input": each_prompt, "constraints": constraints_found,
        "step": steps[0], "code": "None", "tool": tool_found,
    })
    first_step_code = clean_up_llm_output_func(diff_model_source_output_format(first_step_llm))
    print("Step 1:", steps[0])
    print("Code generated:", first_step_code)

    try:
        exec(first_step_code)
        first_step_ret = eval("process_graph(G)")
    except Exception:
        self_debugged_code_1, first_step_ret, first_step_execution_debug_count = \
            self_debug_execution_error(first_step_code, requestData, constraints_found)
        if self_debugged_code_1:
            first_step_code = self_debugged_code_1

    if first_step_ret is not None:
        if isinstance(first_step_ret, str):
            first_step_ret = json.loads(first_step_ret)
        if first_step_ret['type'] == 'graph':
            first_step_ret_graph = clean_up_output_graph_data(first_step_ret)
            first_step_verify_result, _, first_step_verifier_debug_count = \
                error_reduce_verify(constraints_found, requestData, first_step_code,
                                    ret_graph=first_step_ret_graph, ret_list=None)
        else:
            first_step_verify_result, _, first_step_verifier_debug_count = \
                error_reduce_verify(constraints_found, requestData, first_step_code,
                                    ret_graph=None, ret_list=first_step_ret)

    time.sleep(5)

    second_step_execution_debug_count = 0
    second_step_verifier_debug_count = 0

    second_step_llm = cot_plus_tool_chain.invoke({
        "input": each_prompt, "constraints": constraints_found,
        "step": steps[1], "code": first_step_code, "tool": tool_found,
    })
    second_step_code = clean_up_llm_output_func(diff_model_source_output_format(second_step_llm))
    print("Step 2:", steps[1])
    print("Code generated:", second_step_code)

    try:
        exec(second_step_code)
        second_step_ret = eval("process_graph(G)")
    except Exception:
        self_debugged_code_2, second_step_ret, second_step_execution_debug_count = \
            self_debug_execution_error(second_step_code, requestData, constraints_found)
        if self_debugged_code_2:
            second_step_code = self_debugged_code_2

    if second_step_ret is not None:
        if isinstance(second_step_ret, str):
            second_step_ret = json.loads(second_step_ret)
        if second_step_ret['type'] == 'graph':
            second_step_ret_graph = clean_up_output_graph_data(second_step_ret)
            _, _, second_step_verifier_debug_count = \
                error_reduce_verify(constraints_found, requestData, second_step_code,
                                    ret_graph=second_step_ret_graph, ret_list=None)
        else:
            _, _, second_step_verifier_debug_count = \
                error_reduce_verify(constraints_found, requestData, second_step_code,
                                    ret_graph=None, ret_list=second_step_ret)

    time.sleep(5)

    third_step_execution_debug_count = 0
    third_step_verifier_debug_count = 0

    third_step_llm = cot_plus_tool_chain.invoke({
        "input": each_prompt, "constraints": constraints_found,
        "step": steps[2], "code": second_step_code, "tool": tool_found,
    })
    third_step_code = diff_model_source_output_format(third_step_llm)
    if "```python" in third_step_code:
        third_step_code = clean_up_llm_output_func(third_step_code)
        print("Step 3:", steps[2])
        print("Code generated:", third_step_code)
        try:
            exec(third_step_code)
            third_step_ret = eval("process_graph(G)")
        except Exception:
            self_debugged_code_3, third_step_ret, third_step_execution_debug_count = \
                self_debug_execution_error(third_step_code, requestData, constraints_found)
            if self_debugged_code_3:
                third_step_code = self_debugged_code_3
        if third_step_ret is not None:
            if isinstance(third_step_ret, str):
                third_step_ret = json.loads(third_step_ret)
            if third_step_ret['type'] == 'graph':
                third_step_ret_graph = clean_up_output_graph_data(third_step_ret)
                _, _, third_step_verifier_debug_count = \
                    error_reduce_verify(constraints_found, requestData, third_step_code,
                                        ret_graph=third_step_ret_graph, ret_list=None)
            else:
                _, _, third_step_verifier_debug_count = \
                    error_reduce_verify(constraints_found, requestData, third_step_code,
                                        ret_graph=None, ret_list=third_step_ret)

    total_execution_debug_count = (first_step_execution_debug_count +
                                   second_step_execution_debug_count +
                                   third_step_execution_debug_count)
    total_verifier_debug_count = (first_step_verifier_debug_count +
                                  second_step_verifier_debug_count +
                                  third_step_verifier_debug_count)

    code = third_step_code
    try:
        exec(code)
        ret = eval("process_graph(G)")
    except Exception:
        debugged_code, ret, final_exec_debug = \
            self_debug_execution_error(code, requestData, constraints_found)
        total_execution_debug_count += final_exec_debug
        if debugged_code:
            code = debugged_code

    verifier_passed = True
    if ret is not None:
        if isinstance(ret, str):
            ret = json.loads(ret)
        if ret['type'] == 'graph':
            ret_graph_copy = clean_up_output_graph_data(ret)
            verifier = MyChecker(ret_graph_copy, None)
            verifier_passed, _ = verifier.evaluate_all()

    return {
        "code": code,
        "ret": ret,
        "execution_debug_count": total_execution_debug_count,
        "verifier_debug_count": total_verifier_debug_count,
        "verifier_passed": verifier_passed,
        "requestData": requestData,
    }


# ── Comparison with Golden Answer ──────────────────────────────────────────

def compare_with_golden(ground_truth_ret, ret, goldenAnswerCode):
    """Compare LLM output with golden answer. Returns (passed, info_dict)."""
    if ret['type'] == 'graph':
        ret_graph_copy = clean_up_output_graph_data(ret)

    ground_truth_ret['reply'] = goldenAnswerCode
    ret['reply'] = ret_payload.get('reply', '')

    if ground_truth_ret['type'] == 'text':
        if isinstance(ret['data'], int):
            ret['data'] = str(ret['data'])
        if isinstance(ground_truth_ret['data'], int):
            ground_truth_ret['data'] = str(ground_truth_ret['data'])
        return ground_truth_ret['data'] == ret['data'], None

    elif ground_truth_ret['type'] == 'list':
        return check_list_equal(ground_truth_ret['data'], ret['data']), None

    elif ground_truth_ret['type'] == 'table':
        return ground_truth_ret['data'] == ret['data'], None

    elif ground_truth_ret['type'] == 'graph':
        ground_truth_graph = nx.Graph(ground_truth_ret['data'])
        ret_graph = nx.Graph(ret_graph_copy)
        return nx.is_isomorphic(ground_truth_graph, ret_graph, node_match=node_attributes_are_equal), None

    return False, None


# ── Main Experiment ────────────────────────────────────────────────────────

def userQuery(prompt_list):
    init_rag()
    golden_answer_filename = 'golden_answer_generator/prompt_golden_ans.json'
    with open(golden_answer_filename, "r") as fa:
        allAnswer = json.load(fa)

    for each_prompt in prompt_list:
        print("\n" + "=" * 70)
        print("Query:", each_prompt)
        print("=" * 70)
        requestData = {'query': each_prompt}

        if each_prompt not in allAnswer:
            raise SystemExit(f'Un-support ground truth: {each_prompt[:60]}')

        constraints_found = rag_constraint_search(generate_embeddings(each_prompt))
        tool_found = rag_tool_search(generate_embeddings(each_prompt))
        print("Constraints:", constraints_found[:120], "...")
        print("Tools:", tool_found[:120], "...")

        # Run multiple iterations for semantic consistency
        all_outputs = []
        all_debug_counts = {"execution": [], "verifier": []}
        all_verifier_passed = []

        for run_i in range(EACH_PROMPT_RUN_TIME):
            print(f"\n--- Iteration {run_i + 1}/{EACH_PROMPT_RUN_TIME} ---")
            result = run_single_query(each_prompt, constraints_found, tool_found, allAnswer)
            all_outputs.append(result["code"] if result["code"] else "")
            all_debug_counts["execution"].append(result["execution_debug_count"])
            all_debug_counts["verifier"].append(result["verifier_debug_count"])
            all_verifier_passed.append(result["verifier_passed"])
            time.sleep(10)

        # Compute semantic consistency
        C_semantic, _ = compute_C_semantic(all_outputs)
        avg_exec_debug = np.mean(all_debug_counts["execution"])
        avg_verif_debug = np.mean(all_debug_counts["verifier"])
        overall_verifier_passed = all_verifier_passed[-1]  # last run's verifier status

        # Compute confidence score
        S_confidence, abstain_reason = compute_S_confidence(
            overall_verifier_passed, avg_exec_debug, avg_verif_debug, C_semantic
        )

        print(f"\n  C_semantic:     {C_semantic:.4f}")
        print(f"  I_debug (avg):  exec={avg_exec_debug:.1f}, verif={avg_verif_debug:.1f}")
        print(f"  S_confidence:   {S_confidence:.4f}  ({abstain_reason})")

        # Get the last run's result for comparison
        last_result = run_single_query(each_prompt, constraints_found, tool_found, allAnswer)
        code = last_result["code"]
        ret = last_result["ret"]
        total_exec_debug = last_result["execution_debug_count"]
        total_verif_debug = last_result["verifier_debug_count"]

        # Abstention decision
        should_abstain = S_confidence < CONFIDENCE_THRESHOLD

        if should_abstain:
            print(f"  → ABSTAIN (S_confidence={S_confidence:.3f} < {CONFIDENCE_THRESHOLD})")
            with jsonlines.open(OUTPUT_JSONL_PATH, mode='a') as writer:
                writer.write(requestData)
                writer.write({"Result": "Abstain"})
                writer.write({"Abstain reason": abstain_reason})
                writer.write({"S_confidence": round(S_confidence, 4)})
                writer.write({"C_semantic": round(C_semantic, 4)})
                writer.write({"Avg execution debug count": round(avg_exec_debug, 1)})
                writer.write({"Avg verifier debug count": round(avg_verif_debug, 1)})
                writer.write({"LLM code": code if code else ""})
            continue

        # Not abstaining — compare with golden answer
        goldenAnswerCode = allAnswer[each_prompt]
        exec(goldenAnswerCode)
        ground_truth_ret = eval("ground_truth_process_graph(G)")
        if isinstance(ground_truth_ret, str):
            ground_truth_ret = json.loads(ground_truth_ret)

        # Check golden answer comparison
        ret_payload = ret
        if isinstance(ret_payload, str):
            ret_payload = json.loads(ret_payload)

        ground_truth_ret['reply'] = goldenAnswerCode
        ret_payload['reply'] = code if code else ""

        # Type-based comparison
        if ground_truth_ret['type'] == 'text':
            gt_data = str(ground_truth_ret['data']) if isinstance(ground_truth_ret['data'], int) else ground_truth_ret['data']
            ret_data = str(ret_payload['data']) if isinstance(ret_payload['data'], int) else ret_payload['data']
            passed = gt_data == ret_data
        elif ground_truth_ret['type'] == 'list':
            passed = check_list_equal(ground_truth_ret['data'], ret_payload['data'])
        elif ground_truth_ret['type'] == 'table':
            passed = ground_truth_ret['data'] == ret_payload['data']
        elif ground_truth_ret['type'] == 'graph':
            ret_graph_copy = clean_up_output_graph_data(ret_payload)
            ground_truth_graph = nx.Graph(ground_truth_ret['data'])
            ret_graph = nx.Graph(ret_graph_copy)
            passed = nx.is_isomorphic(ground_truth_graph, ret_graph, node_match=node_attributes_are_equal)

        if passed:
            with jsonlines.open(OUTPUT_JSONL_PATH, mode='a') as writer:
                writer.write(requestData)
                writer.write({"Result": "Pass"})
                writer.write({"S_confidence": round(S_confidence, 4)})
                writer.write({"C_semantic": round(C_semantic, 4)})
                writer.write({"Avg execution debug count": round(avg_exec_debug, 1)})
                writer.write({"Avg verifier debug count": round(avg_verif_debug, 1)})
                writer.write({"Execution debug count": total_exec_debug})
                writer.write({"Verifier debug count": total_verif_debug})
                writer.write({"Ground truth code": goldenAnswerCode})
                writer.write({"LLM code": code if code else ""})
                if ground_truth_ret['type'] != 'graph':
                    writer.write({"Ground truth exec": ground_truth_ret['data']})
                    writer.write({"LLM code exec": ret_payload['data']})
            print("  → Pass!")

        else:
            with jsonlines.open(OUTPUT_JSONL_PATH, mode='a') as writer:
                writer.write(requestData)
                writer.write({"Result": "Fail"})
                writer.write({"S_confidence": round(S_confidence, 4)})
                writer.write({"C_semantic": round(C_semantic, 4)})
                writer.write({"Avg execution debug count": round(avg_exec_debug, 1)})
                writer.write({"Avg verifier debug count": round(avg_verif_debug, 1)})
                writer.write({"Execution debug count": total_exec_debug})
                writer.write({"Verifier debug count": total_verif_debug})
                writer.write({"Ground truth code": goldenAnswerCode})
                writer.write({"LLM code": code if code else ""})
                if ground_truth_ret['type'] != 'graph':
                    writer.write({"Ground truth exec": ground_truth_ret['data']})
                    writer.write({"LLM code exec": ret_payload['data']})
            print("  → Fail")
            print("  ground truth:", str(ground_truth_ret.get('data', ''))[:100])

        print(f"========= Current query process is done! =========")

# ── Main ───────────────────────────────────────────────────────────────────

def main():
    if not os.path.exists(OUTPUT_JSONL_PATH):
        os.makedirs(os.path.dirname(OUTPUT_JSONL_PATH), exist_ok=True)
        with open(OUTPUT_JSONL_PATH, 'w') as f:
            pass

    prompt_list = [
        "List all ports contained in packet switch ju1.a1.m1.s2c1. Return a list.",
        "Add a new packet_switch 'ju1.a1.m1.s4c7' on jupiter 1, aggregation block 1, domain 1, with 5 ports, each port has physical_capacity_bps as 1000. Add node type and edges too. Return the new graph.",
        "Update the physical_capacity_bps from 1000 Mbps to 4000 Mbps on node ju1.a1.m1.s2c2.p14. Convert Mbps to bps before the update. Return the new graph.",
        "Identify all CONTROL_POINT nodes that are also PACKET_SWITCH type within the AGG_BLOCK type node ju1.a4.m4. Return a list.",
        "Display all CONTROL_DOMAIN that contains at least 3 CONTROL_POINT. Return a list.",
        "Update all PACKET_SWITCH with node attr packet_switch_attr{switch_loc {stage: 3}} to packet_switch_attr{switch_loc {stage: 5}}. Return the graph.",
        "Find the number of CHASSIS nodes contained in each RACK node? Return a table with headers 'RACK', 'CHASSIS Count'.",
        "What is the bandwidth on packet switch ju1.a2.m1.s2c2? Output bandwidth unit should be in Mbps. Return only the number.",
        "What is the bandwidth on each AGG_BLOCK? Output bandwidth unit should be in Mbps. Return a table with header 'AGG_BLOCK', 'Bandwidth' on the first row.",
        "Find the first and the second largest Chassis by capacity on 'ju1.a1.m1'. Output bandwidth unit should be in Mbps. Return a table with header 'Chassis', 'Bandwidth' on the first row.",
        "Show the average physical_capacity_bps for all PORT in all PACKET_SWITCH. Return a number in string.",
        "For each AGG_BLOCK, list the number of PACKET_SWITCH and PORT it contains. Return a table with headers 'AGG_BLOCK', 'Switch Count', 'Port Count'.",
        "Identify all PACKET_SWITCH nodes contains in AGG_BLOCK node ju1.a1.m1 and calculate their average physical_capacity_bps (on PORT) in bps. Return a table with headers 'Packet Switch', Average Capacity (bps)', sort by highest average capacity.",
        "Find all PACKET_SWITCH nodes that have capacity more than the average. Return a list of nodes.",
        "Remove packet switch 'ju1.a1.m1.s2c4' out from Chassis c4, how to balance the capacity between Chassis? Return the balanced graph.",
        "Remove five PORT nodes (start from p1) from each PACKET_SWITCH node ju1.a1.m1.s2c1, ju1.a1.m1.s2c2, ju1.a1.m1.s2c3, ju1.a1.m1.s2c4, ju1.a1.m1.s2c5. Make sure after the removal the capacity between switches is still balanced. Return the list of ports that will be moved.",
        "Identify all paths from the CONTROL_DOMAIN type node ju1.a1.dom to PORT node ju1.a1.m1.s2c1.p1, and rank them based on the lowest number of hops.",
        "Analyze the redundancy level of each SUPERBLOCK node, by calculating the number of alternative paths between pairs of CHASSIS nodes contains in SUPERBLOCK.",
        "Optimize the current network topology by identifying PACKET_SWITCH nodes that can be removed without affecting the connectivity between CONTROL_DOMAIN nodes. Return a list.",
        "Determine the optimal placement of a new PACKET_SWITCH node ju1.a1.m1.s2c9 with 5 PORT nodes in the format ju1.a1.m1.s2c9.p{i} (each has physical_capacity_bps 1000000000). Consider the current physical_capacity_bps distribution. The goal is to balance average capacity between AGG_BLOCK. Return the networkx graph.",
    ]

    userQuery(prompt_list)


if __name__ == "__main__":
    main()
