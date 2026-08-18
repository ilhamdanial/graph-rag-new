import json
from pathlib import Path
from neo4j import GraphDatabase

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USERNAME = "neo4j"
NEO4J_PASSWORD = "password321"  # 👈 Change to your actual password
DATABASE_NAME = "sample-01"

PAGE_TITLE = "Knowledge Graph"          # 👈 Header title shown in the viewer
TEMPLATE_FILE = "my_graph_template.html"   # 👈 The template this script fills in
OUTPUT_DIR = r"D:\Documents\auditgenie-doc-pipeline\graph-rag-new\knowledge graph"
OUTPUT_FILE = ".html"       # 👈 The finished, self-contained HTML file

# Property names to try, in order, when picking a node's display label / description
LABEL_PROPERTY_CANDIDATES = ["name", "title", "label", "id", "key"]
DESC_PROPERTY_CANDIDATES = ["description", "summary", "bio", "desc", "text"]


def get_id(entity):
    """Helper to safely fetch element ID across Neo4j driver versions."""
    return getattr(entity, "element_id", str(getattr(entity, "id", str(entity))))


def pick_property(props, candidates):
    for key in candidates:
        val = props.get(key)
        if val:
            return str(val)
    return None


def node_to_record(node):
    node_id = get_id(node)
    labels = list(node.labels)
    props = dict(node)

    # "type" drives the color legend in the viewer (PERSON, ORGANIZATION, etc.)
    node_type = labels[0].upper() if labels else "OTHER"
    display_label = pick_property(props, LABEL_PROPERTY_CANDIDATES) or node_id
    description = pick_property(props, DESC_PROPERTY_CANDIDATES) or ""

    return {
        "id": node_id,
        "label": display_label,
        "type": node_type,
        "description": description,
    }


def rel_to_record(rel):
    props = dict(rel)
    description = pick_property(props, DESC_PROPERTY_CANDIDATES) or ""
    return {
        "source": get_id(rel.start_node),
        "target": get_id(rel.end_node),
        "label": rel.type,
        "description": description,
    }


def count_communities(node_ids, links):
    """Cheap weakly-connected-components count, used as a rough
    'communities' figure for the header stat (no APOC/GDS required)."""
    parent = {nid: nid for nid in node_ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for link in links:
        if link["source"] in parent and link["target"] in parent:
            union(link["source"], link["target"])

    return len({find(nid) for nid in node_ids})


def export_graph_to_html():
    print("1. Connecting to Neo4j...")
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))

    nodes_map = {}
    links_list = []
    seen_rel_ids = set()

    print("2. Fetching all nodes and relationships...")
    with driver.session(database=DATABASE_NAME) as session:
        query = """
        MATCH (n)
        OPTIONAL MATCH (n)-[r]->(m)
        RETURN n, r, m
        """
        result = session.run(query)
        for record in result:
            node_n = record["n"]
            rel_r = record["r"]
            node_m = record["m"]

            # Process Start Node (n)
            # NOTE: use "is not None", not truthiness — a Node/Relationship
            # with zero properties behaves like an empty dict and is falsy,
            # which would silently drop it here.
            if node_n is not None:
                n_id = get_id(node_n)
                if n_id not in nodes_map:
                    nodes_map[n_id] = node_to_record(node_n)

            # Process End Node (m)
            if node_m is not None:
                m_id = get_id(node_m)
                if m_id not in nodes_map:
                    nodes_map[m_id] = node_to_record(node_m)

            # Process Relationship (r)
            if rel_r is not None:
                r_id = get_id(rel_r)
                if r_id not in seen_rel_ids:
                    seen_rel_ids.add(r_id)
                    links_list.append(rel_to_record(rel_r))

    driver.close()

    nodes_list = list(nodes_map.values())
    communities = count_communities({n["id"] for n in nodes_list}, links_list)

    graph_data = {
        "nodes": nodes_list,
        "links": links_list,
        "communities": communities,
    }

    # Fill in the HTML template
    print(f"3. Filling in template '{TEMPLATE_FILE}'...")
    template_path = Path(__file__).resolve().parent / TEMPLATE_FILE
    if not template_path.exists():
        raise FileNotFoundError(
            f"Could not find '{TEMPLATE_FILE}' next to this script. "
            "Make sure graph_template.html is in the same folder."
        )
    template = template_path.read_text(encoding="utf-8")

    graph_json = json.dumps(graph_data, ensure_ascii=False)
    html = template.replace("{{PAGE_TITLE}}", PAGE_TITLE).replace(
        "GRAPH_DATA_PLACEHOLDER", graph_json
    )

    print(f"4. Writing graph to {OUTPUT_FILE}...")
    
    # Define directory and file path
    output_dir_path = Path(__file__).resolve().parent / OUTPUT_DIR
    output_dir_path.mkdir(parents=True, exist_ok=True)  # Creates folder if it doesn't exist
    
    output_file_path = output_dir_path / OUTPUT_FILE
    output_file_path.write_text(html, encoding="utf-8")

    print(f"\n✔️ Export complete!")
    print(
        f"   Successfully saved {len(nodes_list)} nodes and {len(links_list)} "
        f"relationships to '{output_file_path}'."
    )


if __name__ == "__main__":
    export_graph_to_html()