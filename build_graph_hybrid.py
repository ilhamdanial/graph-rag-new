import os
import json
from langchain_neo4j import Neo4jGraph
from langchain_ollama import ChatOllama
from langchain_experimental.graph_transformers import LLMGraphTransformer
from langchain_core.documents import Document

# 1. Connect to Neo4j
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password321")
TENANT_ID = "auditgenie_client"

print("1. Connecting to Neo4j...")
graph = Neo4jGraph(url=NEO4J_URI, username=NEO4J_USERNAME, password=NEO4J_PASSWORD)

# 2. Connect to Local Ollama Model (qwen2.5:1.5b)
print("2. Initializing local Ollama model (qwen2.5:1.5b)...")
llm = ChatOllama(model="qwen2.5:1.5b", temperature=0)

# 3. Load JSON chunks
json_file_path = "./iso_docs/ISO_IEC_27001_2022(en).chunks.json"
print(f"3. Reading JSON chunks from {json_file_path}...")
with open(json_file_path, "r", encoding="utf-8") as f:
    chunks = json.load(f)
if isinstance(chunks, dict):
    chunks = [chunks]

# -------------------------------------------------------------
# STEP A: MANUAL HIERARCHY (100% Code-Based, No LLM Needed)
# -------------------------------------------------------------
print("4. [Manual Part] Building ISO Standard -> Clause hierarchy...")
cypher_hierarchy = """
MERGE (s:ISOStandard {id: $standard, tenant_id: $tenant_id})
ON CREATE SET s.name = $standard

MERGE (c:Clause {id: $clause_id, tenant_id: $tenant_id})
ON CREATE SET c.clause_id = $clause_id, c.section_title = $section_title

MERGE (s)-[:INCLUDES_CLAUSE {tenant_id: $tenant_id}]->(c)

MERGE (chk:DocumentChunk {id: $chunk_id, tenant_id: $tenant_id})
ON CREATE SET chk.text = $text, chk.source_file = $source_file, chk.page_number = $page_number

MERGE (c)-[:HAS_CHUNK {tenant_id: $tenant_id}]->(chk)
"""

doc_objects = []
for idx, item in enumerate(chunks):
    # Safe checks: Handles None/null values gracefully
    source = item.get("source_file") or "doc"
    chunk_idx = item.get("chunk_index", idx)
    chunk_id = f"{source}_chunk_{chunk_idx}"

    standard_val = item.get("standard") or "ISO Standard"
    clause_val = str(item.get("clause_id")) if item.get("clause_id") is not None else "General"
    section_val = item.get("section_title") or ""
    text_val = item.get("text") or ""
    page_val = item.get("page_number") or 1

    # Build manual hierarchy directly in Neo4j
    graph.query(cypher_hierarchy, params={
        "tenant_id": TENANT_ID,
        "standard": standard_val,
        "clause_id": clause_val,
        "section_title": section_val,
        "chunk_id": chunk_id,
        "text": text_val,
        "source_file": source,
        "page_number": page_val
    })

    # Prepare document object for local LLM extraction
    doc = Document(
        page_content=text_val,
        metadata={"chunk_id": chunk_id, "tenant_id": TENANT_ID}
    )
    doc_objects.append(doc)

# -------------------------------------------------------------
# STEP B: LOCAL AI EXTRACTION (Discovers deep entities in text)
# -------------------------------------------------------------
print(f"5. [Local AI Part] Extracting entities across {len(doc_objects)} chunks...")
transformer = LLMGraphTransformer(
    llm=llm,
    allowed_nodes=["ControlRequirement", "Policy", "AuditFinding", "Department", "Risk"],
    allowed_relationships=["MAPPED_TO", "EVALUATES", "VIOLATES", "RESPONSIBLE_FOR"]
)

for i, doc in enumerate(doc_objects, 1):
    print(f"   [{i}/{len(doc_objects)}] Processing chunk & saving to Neo4j...")
    
    # 1. Extract entities for a single chunk
    g_docs = transformer.convert_to_graph_documents([doc])
    
    # 2. Attach tenant_id property
    for g_doc in g_docs:
        for node in g_doc.nodes:
            node.properties["tenant_id"] = TENANT_ID
        for rel in g_doc.relationships:
            rel.properties["tenant_id"] = TENANT_ID
            
    # 3. Save directly to Neo4j immediately
    graph.add_graph_documents(g_docs)

print("\n SUCCESS: All chunks processed and saved to Neo4j!")