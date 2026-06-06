import json
import traceback
from dotenv import load_dotenv
import openai
import pandas as pd
from prototxt_parser.prototxt import parse
from collections import Counter
import os
from ai_models_cot import constraint_only_chain
import networkx as nx
import jsonlines
import random
from networkx.readwrite import json_graph
from langchain.callbacks import get_openai_callback
import json
import re
import time
import sys
import numpy as np
from tenacity import retry, wait_random_exponential, stop_after_attempt

# =====================================================================
# ORIGINAL: Azure Cognitive Search imports (commented out for migration)
# =====================================================================
# from azure.core.credentials import AzureKeyCredential
# from azure.search.documents import SearchClient
# from azure.search.documents.indexes import SearchIndexClient
# from azure.search.documents.models import VectorizedQuery
# =====================================================================

# Load environ variables from .env, will not override existing environ variables
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

# =====================================================================
# ORIGINAL: OpenAI text-embedding-ada-002 (commented out for migration)
# =====================================================================
# def generate_embeddings(text):
#     response = openai.Embedding.create(
#         input=text, engine="text-embedding-ada-002")
#     embeddings = response['data'][0]['embedding']
#     return embeddings
# =====================================================================

# =====================================================================
# NEW: text-embedding-v4 (Qwen3-Embedding-8B, 1536-dim) via DashScope
# Set DASHSCOPE_API_KEY in .env
# =====================================================================
import requests

DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
DASHSCOPE_URL = os.getenv(
    "DASHSCOPE_EMBEDDING_URL",
    "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/embeddings"
)


def generate_embeddings(text):
    headers = {
        "Authorization": f"Bearer {DASHSCOPE_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "text-embedding-v4",
        "input": text,
        "dimensions": 1536,
        "encoding_format": "float",
    }
    resp = requests.post(DASHSCOPE_URL, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    result = resp.json()
    return result["data"][0]["embedding"]
# =====================================================================

def count_tokens(chain, query):
    with get_openai_callback() as cb:
        result = chain.run(query)
        print(f'Spent a total of {cb.total_tokens} tokens')

    return cb.total_tokens

def getGraphData():
    input_string = open("data/malt-example-final.textproto.txt").read()
    parsed_dict = parse(input_string)

    # Load MALT data
    G = nx.DiGraph()

    # Insert all the entities as nodes
    for entity in parsed_dict['entity']:
        # Check if the node exists
        if entity['id']['name'] not in G.nodes:
            G.add_node(entity['id']['name'], type=[entity['id']['kind']], name=entity['id']['name'])
        else:
            G.nodes[entity['id']['name']]['type'].append(entity['id']['kind'])
        # Add all the attributes
        for key, value in entity.items():
            if key == 'id':
                continue
            for k, v in value.items():
                G.nodes[entity['id']['name']][k] = v

    # Insert all the relations as edges
    for relation in parsed_dict['relationship']:
        G.add_edge(relation['a']['name'], relation['z']['name'], type=relation['kind'])

    rawData = json_graph.node_link_data(G)

    return rawData, G

def node_attributes_are_equal(node1_attrs, node2_attrs):
    # Check if both nodes have the exact same set of attributes
    if set(node1_attrs.keys()) != set(node2_attrs.keys()):
        return False

    # Check if all attribute values are equal
    for attr_name, attr_value in node1_attrs.items():
        if attr_value != node2_attrs[attr_name]:
            return False

    return True


# =====================================================================
# ORIGINAL: Azure Cognitive Search helper (commented out for migration)
# =====================================================================
# def extract_constraints(results):
#     constraints_list = []
#     for result in results:
#         if 'constraint' in result:
#             constraints_list.append(result['constraint'])
#     constraints_string = ' '.join(constraints_list)
#     return constraints_string
# =====================================================================

def clean_up_llm_output_func(answer):
    '''
    Extract only the def process_graph() funtion from the output of LLM
    :param answer: output of LLM
    :return: cleaned function
    '''
    start = answer.find("def process_graph")
    end = -1
    index = 0
    for _ in range(2):  # change the number 2 to any 'n' to find the nth occurrence
        end = answer.find("```", index)
        index = end + 1
    clean_code = answer[start:end].strip()
    return clean_code

def check_list_equal(lst1, lst2):
    if lst1 and isinstance(lst1[0], list):
        return Counter(json.dumps(i) for i in lst1) == Counter(json.dumps(i) for i in lst2)
    else:
        return Counter(lst1) == Counter(lst2)


def clean_up_output_graph_data(ret):
    if isinstance(ret['data'], nx.Graph):
        # Create a nx.graph copy, so I can compare two nx.graph later directly
        ret_graph_copy = ret['data']
        jsonGraph = nx.node_link_data(ret['data'])
        ret['data'] = jsonGraph

    else:  # Convert the jsonGraph back to nx.graph, to check if they are identical later
        ret_graph_copy = json_graph.node_link_graph(ret['data'])

    return ret_graph_copy


# =====================================================================
# ORIGINAL: Azure Cognitive Search tool extractor (commented out for migration)
# =====================================================================
# def extract_tools(results):
#     tool_list = []
#     for result in results:
#         if result['@search.score'] < 0.85:
#             return "no tools available"
#         else:
#             if 'tool' in result:
#                 tool_list.append(result['tool'])
#     tool_string = ' '.join(tool_list)
#     return tool_string
# =====================================================================