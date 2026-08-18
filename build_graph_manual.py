import os
import re
import json
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_neo4j import Neo4jGraph

# ----------------------------------------------------------------------
# 1. Configuration & Connection
# ----------------------------------------------------------------------
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USERNAME = "neo4j"
NEO4J_PASSWORD = "passwordabc"  # Update with your password
DATABASE_NAME = "abc"                # Default Neo4j database
FILE_PATH = r"chunks.json"
TENANT_ID = "GLOBAL"

print("1. Connecting to Neo4j...")
graph = Neo4jGraph(
    url=NEO4J_URI,
    username=NEO4J_USERNAME,
    password=NEO4J_PASSWORD,
    database=DATABASE_NAME
)

# Simple Chunk class wrapper
class DocumentChunkItem:
    def __init__(self, page_content):
        self.page_content = page_content
        self.metadata = {}

# ----------------------------------------------------------------------
# 2. Universal File Loader (.json, .pdf, .docx, .xlsx, .txt)
# ----------------------------------------------------------------------
def load_file_into_chunks(file_path):
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    ext = os.path.splitext(file_path)[1].lower()
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=150)
    
    # A. JSON File (Pre-chunked)
    if ext == ".json":
        print("   Detected .json format (reading pre-chunked data)...")
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        chunks = []
        if isinstance(data, list):
            for item in data:
                text = item.get("text") or item.get("page_content") or item.get("content") if isinstance(item, dict) else str(item)
                chunks.append(DocumentChunkItem(text))
        elif isinstance(data, dict):
            text = data.get("text") or data.get("page_content") or data.get("content") or str(data)
            chunks.append(DocumentChunkItem(text))
        return chunks

    # B. PDF File
    elif ext == ".pdf":
        print("   Detected .pdf format...")
        import pypdf
        reader = pypdf.PdfReader(file_path)
        full_text = "\n".join([page.extract_text() for page in reader.pages if page.extract_text()])
        raw_docs = [DocumentChunkItem(full_text)]
        return text_splitter.split_documents(raw_docs)

    # C. DOCX File
    elif ext in [".docx", ".doc"]:
        print("   Detected .docx format...")
        import docx
        doc = docx.Document(file_path)
        full_text = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
        raw_docs = [DocumentChunkItem(full_text)]
        return text_splitter.split_documents(raw_docs)

    # D. XLSX File
    elif ext in [".xlsx", ".xls"]:
        print("   Detected .xlsx format...")
        import openpyxl
        wb = openpyxl.load_workbook(file_path, data_only=True)
        lines = []
        for sheet in wb.worksheets:
            lines.append(f"Sheet: {sheet.title}")
            for row in sheet.iter_rows(values_only=True):
                row_str = " | ".join([str(val) for val in row if val is not None])
                if row_str.strip():
                    lines.append(row_str)
        full_text = "\n".join(lines)
        raw_docs = [DocumentChunkItem(full_text)]
        return text_splitter.split_documents(raw_docs)

    # E. Standard Plain Text / Fallback
    else:
        print("   Detected plain text format...")
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            full_text = f.read()
        raw_docs = [DocumentChunkItem(full_text)]
        return text_splitter.split_documents(raw_docs)

# Load data into unified chunks
print(f"2. Loading {FILE_PATH}...")
chunks = load_file_into_chunks(FILE_PATH)
print(f"   Successfully loaded {len(chunks)} chunks.")

# ----------------------------------------------------------------------
# 3. Document Hierarchy Construction (Steps 1–4)
# ----------------------------------------------------------------------
doc_title = os.path.basename(FILE_PATH)

print("3. Building document hierarchy in Neo4j...")
graph.query(
    """
    MERGE (d:Document {id: $doc_id})
    SET d.title = $title, d.tenant_id = $tenant_id
    """,
    {"doc_id": doc_title, "title": doc_title, "tenant_id": TENANT_ID}
)

for i, chunk in enumerate(chunks, 1):
    chunk_id = f"{doc_title}_chunk_{i}"
    chunk.metadata["chunk_id"] = chunk_id
    
    graph.query(
        """
        MATCH (d:Document {id: $doc_id})
        MERGE (c:DocumentChunk {id: $chunk_id})
        SET c.text = $text, c.chunk_index = $index, c.tenant_id = $tenant_id
        MERGE (d)-[:HAS_CHUNK]->(c)
        """,
        {
            "doc_id": doc_title,
            "chunk_id": chunk_id,
            "text": chunk.page_content,
            "index": i,
            "tenant_id": TENANT_ID
        }
    )

# ----------------------------------------------------------------------
# 4. Generic Rule Extractor
# ----------------------------------------------------------------------
DEPARTMENT_KEYWORDS = ["IT", "Finance", "HR", "Audit", "Management", "Operations", "Security", "Engineering", "Legal", "Compliance"]

def extract_generic_entities_and_rels(chunk_text):
    nodes = []
    relationships = []
    sentences = re.split(r'(?<=[.!?]) +', chunk_text)
    
    # A. Departments
    found_depts = []
    for dept in DEPARTMENT_KEYWORDS:
        if re.search(rf"\b{dept}\b", chunk_text, re.IGNORECASE):
            dept_name = f"{dept} Department" if not dept.endswith("Management") else dept
            found_depts.append(dept_name)
            nodes.append({"label": "Department", "name": dept_name})

    # B. Policies & Standards
    policy_matches = re.findall(r"(?:Policy|Procedure|SOP|Standard|Guideline|Framework|Section|Clause|Act)\s+[A-Z0-9\.\-]+", chunk_text, re.IGNORECASE)
    for p in set(policy_matches):
        nodes.append({"label": "Policy", "name": p.strip()})

    # C. Risks
    for sent in sentences:
        if re.search(r"\b(risk|threat|vulnerability|failure|breach|unauthorized|non-compliance)\b", sent, re.IGNORECASE):
            risk_name = sent[:60].strip() + "..." if len(sent) > 60 else sent.strip()
            nodes.append({"label": "Risk", "name": risk_name})
            for dept_name in found_depts:
                relationships.append({
                    "source_label": "Department", "source_name": dept_name,
                    "rel_type": "EVALUATES",
                    "target_label": "Risk", "target_name": risk_name
                })

    return nodes, relationships

# ----------------------------------------------------------------------
# 5. Extraction Loop & Neo4j Insertion
# ----------------------------------------------------------------------
print(f"4. Extracting entities and linking to Neo4j...")
for chunk in chunks:
    chunk_id = chunk.metadata["chunk_id"]
    text = chunk.page_content
    
    nodes, rels = extract_generic_entities_and_rels(text)
    
    for node in nodes:
        cypher_node = f"""
        MATCH (c:DocumentChunk {{id: $chunk_id}})
        MERGE (e:`{node['label']}` {{name: $name}})
        SET e.tenant_id = $tenant_id
        MERGE (c)-[:MENTIONS]->(e)
        """
        graph.query(cypher_node, {
            "chunk_id": chunk_id,
            "name": node["name"],
            "tenant_id": TENANT_ID
        })
        
    for rel in rels:
        cypher_rel = f"""
        MERGE (s:`{rel['source_label']}` {{name: $source_name}})
        SET s.tenant_id = $tenant_id
        MERGE (t:`{rel['target_label']}` {{name: $target_name}})
        SET t.tenant_id = $tenant_id
        MERGE (s)-[:`{rel['rel_type']}`]->(t)
        """
        graph.query(cypher_rel, {
            "source_name": rel["source_name"],
            "target_name": rel["target_name"],
            "tenant_id": TENANT_ID
        })

print("\n✔️ Complete! Document and extracted graph structures are live in Neo4j.")