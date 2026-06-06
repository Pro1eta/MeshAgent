"""
Regenerate RAG vectors using text-embedding-v4 (Qwen3-Embedding-8B, 1536-dim)
and rebuild ChromaDB index.

Usage:
    python scripts/reindex.py

Requires DASHSCOPE_API_KEY in environment or .env file.
"""

import os
import sys
import json
import time
import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
if not DASHSCOPE_API_KEY:
    print("ERROR: DASHSCOPE_API_KEY not set in .env")
    sys.exit(1)

DASHSCOPE_URL = os.getenv(
    "DASHSCOPE_EMBEDDING_URL",
    "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/embeddings"
)
EMBEDDING_MODEL = "text-embedding-v4"
EMBEDDING_DIM = 1536

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
OUTPUT_DIR = os.path.join(
    os.path.dirname(__file__), "..", "create_RAG_index", "output"
)

CONSTRAINT_INPUT = os.path.join(DATA_DIR, "rag_constraints.json")
TOOL_INPUT = os.path.join(DATA_DIR, "rag_tools.json")
CONSTRAINT_OUTPUT = os.path.join(OUTPUT_DIR, "constraintVectors.json")
TOOL_OUTPUT = os.path.join(OUTPUT_DIR, "toolVectors.json")


def embed_batch(texts):
    """Generate embeddings for a list of texts using text-embedding-v4."""
    headers = {
        "Authorization": f"Bearer {DASHSCOPE_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": EMBEDDING_MODEL,
        "input": texts,
        "dimensions": EMBEDDING_DIM,
        "encoding_format": "float",
    }

    resp = requests.post(DASHSCOPE_URL, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    result = resp.json()
    return [item["embedding"] for item in result["data"]]


def reindex_constraints():
    print("Reindexing constraints...")
    with open(CONSTRAINT_INPUT) as f:
        constraints = json.load(f)

    texts = [c["constraint"] for c in constraints]

    for i in range(0, len(texts), 10):
        batch = texts[i : i + 10]
        embeddings = embed_batch(batch)
        for j, emb in enumerate(embeddings):
            idx = i + j
            constraints[idx]["constraintVector"] = emb
            constraints[idx]["labelVector"] = emb
        print(f"  Constraints {i+1}-{min(i+10, len(texts))}/{len(texts)}")
        if i + 10 < len(texts):
            time.sleep(0.5)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(CONSTRAINT_OUTPUT, "w") as f:
        json.dump(constraints, f)
    print(f"  Saved to {CONSTRAINT_OUTPUT}")


def reindex_tools():
    print("Reindexing tools...")
    with open(TOOL_INPUT) as f:
        tools = json.load(f)

    texts = [t["tool"] for t in tools]
    desc_texts = [t["description"] for t in tools]

    all_texts = texts + desc_texts
    all_embeddings = embed_batch(all_texts)

    mid = len(texts)
    tool_embeddings = all_embeddings[:mid]
    desc_embeddings = all_embeddings[mid:]

    for i in range(len(tools)):
        tools[i]["toolVector"] = tool_embeddings[i]
        tools[i]["descriptionVector"] = desc_embeddings[i]

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(TOOL_OUTPUT, "w") as f:
        json.dump(tools, f)
    print(f"  Saved to {TOOL_OUTPUT}")


def main():
    print(f"Using model: {EMBEDDING_MODEL}, dimension: {EMBEDDING_DIM}")
    reindex_constraints()
    reindex_tools()

    # Now rebuild ChromaDB
    print("\nRebuilding ChromaDB index...")
    from rag_local import init_rag

    init_rag(force_reindex=True)
    print("Done! RAG index rebuilt with text-embedding-v4 vectors.")


if __name__ == "__main__":
    main()
