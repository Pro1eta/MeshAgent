"""
Local vector database for MALT RAG using ChromaDB.
Replaces Azure Cognitive Search with ChromaDB persistent client.

Usage:
    from rag_local import init_rag, rag_constraint_search, rag_tool_search
    init_rag()  # call once at startup
    constraints = rag_constraint_search(query_embedding, top_k=13)
    tools = rag_tool_search(query_embedding, top_k=1)
"""

import os
import json
import chromadb
from chromadb.config import Settings

# Paths
CHROMA_DIR = os.path.join(os.path.dirname(__file__), "chroma_rag")
CONSTRAINT_VECTORS_PATH = os.path.join(
    os.path.dirname(__file__), "create_RAG_index", "output", "constraintVectors.json"
)
TOOL_VECTORS_PATH = os.path.join(
    os.path.dirname(__file__), "create_RAG_index", "output", "toolVectors.json"
)

_collections = {}


def _get_client():
    return chromadb.PersistentClient(
        path=CHROMA_DIR,
        settings=Settings(anonymized_telemetry=False),
    )


def init_rag(force_reindex=False):
    """
    Initialize ChromaDB collections from JSON vector files.
    Call once at startup. If collections already exist, skips indexing.
    Set force_reindex=True to rebuild from scratch.
    """
    client = _get_client()

    existing = client.list_collections()
    existing_names = {c.name for c in existing}

    if "rag_constraints" in existing_names and not force_reindex:
        _collections["constraints"] = client.get_collection("rag_constraints")
    else:
        if "rag_constraints" in existing_names:
            client.delete_collection("rag_constraints")
        _collections["constraints"] = _build_constraint_collection(client)

    if "rag_tools" in existing_names and not force_reindex:
        _collections["tools"] = client.get_collection("rag_tools")
    else:
        if "rag_tools" in existing_names:
            client.delete_collection("rag_tools")
        _collections["tools"] = _build_tool_collection(client)


def _build_constraint_collection(client):
    with open(CONSTRAINT_VECTORS_PATH) as f:
        data = json.load(f)

    collection = client.create_collection(
        name="rag_constraints",
        metadata={"hnsw:space": "cosine"},
    )

    if not data:
        return collection

    collection.add(
        ids=[item["id"] for item in data],
        documents=[item["constraint"] for item in data],
        embeddings=[item["constraintVector"] for item in data],
        metadatas=[{"label": item["label"]} for item in data],
    )
    return collection


def _build_tool_collection(client):
    with open(TOOL_VECTORS_PATH) as f:
        data = json.load(f)

    collection = client.create_collection(
        name="rag_tools",
        metadata={"hnsw:space": "cosine"},
    )

    if not data:
        return collection

    collection.add(
        ids=[item["id"] for item in data],
        documents=[item["tool"] for item in data],
        embeddings=[item["toolVector"] for item in data],
        metadatas=[{"description": item["description"]} for item in data],
    )
    return collection


def rag_constraint_search(query_embedding, top_k=13):
    """
    Search constraint collection by vector similarity.
    Returns a concatenated string of matched constraints (compatible with old interface).
    """
    col = _collections.get("constraints")
    if col is None:
        raise RuntimeError("RAG not initialized. Call init_rag() first.")

    results = col.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
        include=["documents"],
    )
    constraints_list = results["documents"][0] if results["documents"] else []
    return " ".join(constraints_list)


def rag_tool_search(query_embedding, top_k=1):
    """
    Search tool collection by vector similarity.
    Returns a concatenated string of matched tools (compatible with old interface).
    """
    col = _collections.get("tools")
    if col is None:
        raise RuntimeError("RAG not initialized. Call init_rag() first.")

    results = col.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
        include=["documents", "distances"],
    )
    if not results["documents"] or not results["documents"][0]:
        return "no tools available"

    documents = results["documents"][0]
    distances = results["distances"][0]

    tool_list = []
    for doc, dist in zip(documents, distances):
        similarity = 1.0 - dist
        if similarity < 0.7:
            return "no tools available"
        tool_list.append(doc)

    return " ".join(tool_list) if tool_list else "no tools available"
