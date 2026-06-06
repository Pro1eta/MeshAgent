"""
MeshAgent Stage 6: Full pipeline with benchmark prompts (no abstention).

Combines all stages: query-specific constraints, CoT, verifier, tools.
Uses the same 20 benchmark prompts as Stages 1-4 for direct comparison.
Single run per query (EACH_PROMPT_RUN_TIME=1).

Output: logs/debug/full_meshagent_benchmark.jsonl
"""

import json, traceback, os, sys, time, re
import numpy as np
import networkx as nx
import jsonlines
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from ai_models_cot import summary_gen_chain, cot_plus_tool_chain, pySelfDebugger
from helper import (
    getGraphData, generate_embeddings, clean_up_llm_output_func,
    check_list_equal, node_attributes_are_equal, clean_up_output_graph_data,
)
from rag_local import init_rag, rag_constraint_search, rag_tool_search
from error_check import MyChecker
from networkx.readwrite import json_graph

EACH_PROMPT_RUN_TIME = 1
OUTPUT_JSONL_PATH = "logs/debug/full_meshagent_benchmark.jsonl"
DEBUG_LOOP_TOTAL = 5

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
                if i == DEBUG_LOOP_TOTAL - 1:
                    with jsonlines.open(OUTPUT_JSONL_PATH, mode='a') as writer:
                        writer.write(requestData)
                        writer.write({"Result": "Fail, code cannot pass all verifiers"})
                        writer.write({"LLM code": debugged_code})
                        writer.write({"Error": str(e)})
                        writer.write({"Verifier debug count": verifier_self_debug_count})
    return verifier_results, None, verifier_self_debug_count


def self_debug_execution_error(code, requestData, constraints_found):
    execution_error_self_debug = 0
    exc_type, ex, tb = sys.exc_info()
    imported_tb_info = traceback.extract_tb(tb)[-1]
    error_details = f'{exc_type.__name__}: Exception in line: {imported_tb_info[1]}. Message: {ex}'
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
            error_details = f'{exc_type.__name__}: Exception in line: {imported_tb_info[1]}. Message: {ex}'
            print("Fail due to errors:", error_details)
    return None, None, execution_error_self_debug


# ── Ground Truth Helpers ───────────────────────────────────────────────────

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
        writer.write({"Result": "Fail"})
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

        prompt_accu = 0
        _, G = getGraphData()
        ret = None
        ground_truth_ret = None
        total_execution_debug_count = 0
        total_verifier_debug_count = 0

        for i in range(EACH_PROMPT_RUN_TIME):
            print(f"\n--- Iteration {i + 1}/{EACH_PROMPT_RUN_TIME} ---")

            summary_output = summary_gen_chain.invoke({"input": each_prompt})
            step_summary = summary_output.to_json()['kwargs']['content']
            steps = re.split(r'Step \d+: ', step_summary)
            steps = steps[1:]

            first_step_execution_debug_count = 0
            first_step_verifier_debug_count = 0

            first_step_llm = cot_plus_tool_chain.invoke({
                "input": each_prompt, "constraints": constraints_found,
                "step": steps[0], "code": "None", "tool": tool_found,
            })
            first_step_code = clean_up_llm_output_func(first_step_llm.to_json()['kwargs']['content'])
            print("Step 1:", steps[0])
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
                    _, _, first_step_verifier_debug_count = \
                        error_reduce_verify(constraints_found, requestData, first_step_code,
                                            ret_graph=first_step_ret_graph, ret_list=None)
                else:
                    _, _, first_step_verifier_debug_count = \
                        error_reduce_verify(constraints_found, requestData, first_step_code,
                                            ret_graph=None, ret_list=first_step_ret)

            time.sleep(5)

            second_step_execution_debug_count = 0
            second_step_verifier_debug_count = 0

            second_step_llm = cot_plus_tool_chain.invoke({
                "input": each_prompt, "constraints": constraints_found,
                "step": steps[1], "code": first_step_code, "tool": tool_found,
            })
            second_step_code = clean_up_llm_output_func(second_step_llm.to_json()['kwargs']['content'])
            print("Step 2:", steps[1])
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
            third_step_code = third_step_llm.to_json()['kwargs']['content']
            if "```python" in third_step_code:
                third_step_code = clean_up_llm_output_func(third_step_code)
                print("Step 3:", steps[2])
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
                                                ret_graph=None, ret_list=second_step_ret)

            total_execution_debug_count = (first_step_execution_debug_count +
                                           second_step_execution_debug_count +
                                           third_step_execution_debug_count)
            total_verifier_debug_count = (first_step_verifier_debug_count +
                                          second_step_verifier_debug_count +
                                          third_step_verifier_debug_count)

            code = third_step_code
            llm_output_token_count = 0
            try:
                exec(code)
                ret = eval("process_graph(G)")
            except Exception:
                debugged_code, ret, final_exec_debug = \
                    self_debug_execution_error(code, requestData, constraints_found)
                total_execution_debug_count += final_exec_debug
                if debugged_code:
                    code = debugged_code

            if ret is None:
                continue

            if isinstance(ret, str):
                ret = json.loads(ret)
            goldenAnswerCode = allAnswer[each_prompt]

            _, G = getGraphData()
            exec(goldenAnswerCode)
            ground_truth_ret = eval("ground_truth_process_graph(G)")
            if isinstance(ground_truth_ret, str):
                ground_truth_ret = json.loads(ground_truth_ret)

            ground_truth_ret['reply'] = goldenAnswerCode
            ret['reply'] = code

            if ground_truth_ret['type'] == 'text':
                if isinstance(ret['data'], int):
                    ret['data'] = str(ret['data'])
                if isinstance(ground_truth_ret['data'], int):
                    ground_truth_ret['data'] = str(ground_truth_ret['data'])
                if ground_truth_ret['data'] == ret['data']:
                    prompt_accu = ground_truth_check_accu(prompt_accu, requestData,
                                                          ground_truth_ret, ret,
                                                          llm_output_token_count,
                                                          total_execution_debug_count,
                                                          total_verifier_debug_count)
                else:
                    ground_truth_check_debug(requestData, ground_truth_ret, ret,
                                            llm_output_token_count,
                                            total_execution_debug_count,
                                            total_verifier_debug_count)
            elif ground_truth_ret['type'] == 'list':
                if check_list_equal(ground_truth_ret['data'], ret['data']):
                    prompt_accu = ground_truth_check_accu(prompt_accu, requestData,
                                                          ground_truth_ret, ret,
                                                          llm_output_token_count,
                                                          total_execution_debug_count,
                                                          total_verifier_debug_count)
                else:
                    ground_truth_check_debug(requestData, ground_truth_ret, ret,
                                            llm_output_token_count,
                                            total_execution_debug_count,
                                            total_verifier_debug_count)
            elif ground_truth_ret['type'] == 'table':
                if ground_truth_ret['data'] == ret['data']:
                    prompt_accu = ground_truth_check_accu(prompt_accu, requestData,
                                                          ground_truth_ret, ret,
                                                          llm_output_token_count,
                                                          total_execution_debug_count,
                                                          total_verifier_debug_count)
                else:
                    ground_truth_check_debug(requestData, ground_truth_ret, ret,
                                            llm_output_token_count,
                                            total_execution_debug_count,
                                            total_verifier_debug_count)
            elif ground_truth_ret['type'] == 'graph':
                if ret['type'] == 'graph':
                    ret_graph_copy = clean_up_output_graph_data(ret)
                ground_truth_graph = nx.Graph(ground_truth_ret['data'])
                ret_graph = nx.Graph(ret_graph_copy)
                if nx.is_isomorphic(ground_truth_graph, ret_graph, node_match=node_attributes_are_equal):
                    prompt_accu = ground_truth_check_accu(prompt_accu, requestData,
                                                          ground_truth_ret, ret,
                                                          llm_output_token_count,
                                                          total_execution_debug_count,
                                                          total_verifier_debug_count)
                else:
                    ground_truth_check_debug(requestData, ground_truth_ret, ret,
                                            llm_output_token_count,
                                            total_execution_debug_count,
                                            total_verifier_debug_count)

            time.sleep(10)

        print(f"========= Current query process is done! =========")
        print(f"Accuracy: {prompt_accu}/{EACH_PROMPT_RUN_TIME}")


def main():
    os.makedirs(os.path.dirname(OUTPUT_JSONL_PATH), exist_ok=True)
    if not os.path.exists(OUTPUT_JSONL_PATH):
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
        "Provide a graph that contains all SUPERBLOCK and AGG_BLOCK. Create the new graph.",
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
