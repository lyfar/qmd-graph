#!/usr/bin/env python3
"""
qmd-graph: Wikilink-aware graph layer for qmd.

Parses [[wikilinks]] from qmd-indexed documents, builds an adjacency graph
in SQLite, and provides graph traversal commands: path, related, bridges,
orphans, activate (spreading activation from vector search seeds).

Zero dependencies beyond Python stdlib. Reads from qmd's index.sqlite.

Inspired by Physarum polycephalum: find the shortest path between ideas
through their connections, not just their content similarity.
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import deque
from pathlib import Path

# --- Config ---

QMD_INDEX = os.environ.get(
    "QMD_INDEX",
    os.path.expanduser("~/.cache/qmd/index.sqlite")
)
GRAPH_DB = os.environ.get(
    "QMD_GRAPH_DB",
    os.path.expanduser("~/.cache/qmd/graph.sqlite")
)
WIKILINK_RE = re.compile(r'\[\[([^\]|]+?)(?:\|[^\]]+?)?\]\]')


# --- Graph DB ---

def init_graph_db(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            doc_id INTEGER,
            collection TEXT,
            path TEXT
        );
        CREATE TABLE IF NOT EXISTS edges (
            source_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            UNIQUE(source_id, target_id),
            FOREIGN KEY(source_id) REFERENCES nodes(id),
            FOREIGN KEY(target_id) REFERENCES nodes(id)
        );
        CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
        CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
        CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);

        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """)


def normalize_name(raw):
    """Normalize a wikilink target to match file stems."""
    return raw.strip().lower().replace(" ", "-").replace("_", "-")


def build_graph(collection=None, verbose=False):
    """Parse wikilinks from qmd index and build the graph."""
    qmd = sqlite3.connect(QMD_INDEX)
    graph = sqlite3.connect(GRAPH_DB)
    init_graph_db(graph)

    # Clear old data
    graph.executescript("DELETE FROM edges; DELETE FROM nodes;")

    # Load all active documents
    query = "SELECT id, collection, path FROM documents WHERE active=1"
    params = ()
    if collection:
        query += " AND collection=?"
        params = (collection,)

    docs = qmd.execute(query, params).fetchall()

    # Build node map: normalized_stem -> (doc_id, collection, path, display_name)
    node_map = {}
    for doc_id, coll, path in docs:
        stem = Path(path).stem
        norm = normalize_name(stem)
        display = stem.replace("-", " ").title()
        # Keep first seen (or prefer brain collection)
        if norm not in node_map or coll == "brain":
            node_map[norm] = (doc_id, coll, path, display)

    # Insert nodes
    node_ids = {}
    for norm, (doc_id, coll, path, display) in node_map.items():
        graph.execute(
            "INSERT OR IGNORE INTO nodes (name, doc_id, collection, path) VALUES (?,?,?,?)",
            (norm, doc_id, coll, path)
        )
        node_ids[norm] = graph.execute(
            "SELECT id FROM nodes WHERE name=?", (norm,)
        ).fetchone()[0]

    # Parse wikilinks from content and build edges
    edge_count = 0
    for norm, (doc_id, coll, path, display) in node_map.items():
        content = qmd.execute(
            "SELECT c.doc FROM documents d JOIN content c ON d.hash=c.hash WHERE d.id=?",
            (doc_id,)
        ).fetchone()
        if not content:
            continue

        links = WIKILINK_RE.findall(content[0])
        source_id = node_ids[norm]

        for link in links:
            target_norm = normalize_name(link)
            if target_norm in node_ids and target_norm != norm:
                target_id = node_ids[target_norm]
                try:
                    graph.execute(
                        "INSERT OR IGNORE INTO edges (source_id, target_id) VALUES (?,?)",
                        (source_id, target_id)
                    )
                    edge_count += 1
                except sqlite3.IntegrityError:
                    pass

    # Store metadata
    from datetime import datetime
    graph.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        ("last_build", datetime.now().isoformat())
    )
    graph.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        ("node_count", str(len(node_ids)))
    )
    graph.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        ("edge_count", str(edge_count))
    )

    graph.commit()
    graph.close()
    qmd.close()

    if verbose:
        print(f"Built graph: {len(node_ids)} nodes, {edge_count} edges")
    return len(node_ids), edge_count


# --- Graph Queries ---

def get_graph():
    return sqlite3.connect(GRAPH_DB)


def find_node(conn, query):
    """Find a node by fuzzy name match."""
    norm = normalize_name(query)
    # Exact match
    row = conn.execute("SELECT id, name, path FROM nodes WHERE name=?", (norm,)).fetchone()
    if row:
        return row
    # Substring match
    row = conn.execute(
        "SELECT id, name, path FROM nodes WHERE name LIKE ? LIMIT 1",
        (f"%{norm}%",)
    ).fetchone()
    return row


def shortest_path(start_query, end_query):
    """BFS shortest path between two notes."""
    conn = get_graph()
    start = find_node(conn, start_query)
    end = find_node(conn, end_query)

    if not start:
        print(f"Node not found: {start_query}")
        return
    if not end:
        print(f"Node not found: {end_query}")
        return

    # BFS (bidirectional edges — follow both directions)
    queue = deque([(start[0], [start[0]])])
    visited = {start[0]}

    # Build adjacency (undirected)
    adj = {}
    for s, t in conn.execute("SELECT source_id, target_id FROM edges").fetchall():
        adj.setdefault(s, []).append(t)
        adj.setdefault(t, []).append(s)

    while queue:
        node, path = queue.popleft()
        if node == end[0]:
            # Found! Print path
            names = []
            for nid in path:
                row = conn.execute("SELECT name, path FROM nodes WHERE id=?", (nid,)).fetchone()
                names.append(row)
            print(f"\nPath ({len(names)-1} hops):\n")
            for i, (name, fpath) in enumerate(names):
                display = name.replace("-", " ").title()
                prefix = "  → " if i > 0 else "  ● "
                print(f"{prefix}{display}  ({fpath})")
            conn.close()
            return names

        for neighbor in adj.get(node, []):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, path + [neighbor]))

    print(f"No path found between '{start_query}' and '{end_query}'")
    conn.close()


def related(query, depth=2):
    """Get all notes within N hops of a note."""
    conn = get_graph()
    start = find_node(conn, query)
    if not start:
        print(f"Node not found: {query}")
        return

    # Build adjacency (undirected)
    adj = {}
    for s, t in conn.execute("SELECT source_id, target_id FROM edges").fetchall():
        adj.setdefault(s, []).append(t)
        adj.setdefault(t, []).append(s)

    # BFS with depth limit
    queue = deque([(start[0], 0)])
    visited = {start[0]: 0}

    while queue:
        node, d = queue.popleft()
        if d >= depth:
            continue
        for neighbor in adj.get(node, []):
            if neighbor not in visited:
                visited[neighbor] = d + 1
                queue.append((neighbor, d + 1))

    # Group by depth
    by_depth = {}
    for nid, d in visited.items():
        if nid == start[0]:
            continue
        by_depth.setdefault(d, []).append(nid)

    start_display = start[1].replace("-", " ").title()
    print(f"\nRelated to: {start_display} (depth {depth})\n")

    for d in sorted(by_depth.keys()):
        print(f"  {'·' * d} Depth {d}:")
        for nid in sorted(by_depth[d]):
            row = conn.execute("SELECT name, path FROM nodes WHERE id=?", (nid,)).fetchone()
            display = row[0].replace("-", " ").title()
            print(f"    {display}  ({row[1]})")
    
    total = sum(len(v) for v in by_depth.values())
    print(f"\n  {total} connected notes within {depth} hops")
    conn.close()


def bridges():
    """Find bridge notes — remove them and the graph splits."""
    conn = get_graph()

    adj = {}
    nodes_list = [r[0] for r in conn.execute("SELECT id FROM nodes").fetchall()]
    for s, t in conn.execute("SELECT source_id, target_id FROM edges").fetchall():
        adj.setdefault(s, set()).add(t)
        adj.setdefault(t, set()).add(s)

    # Tarjan's bridge-finding algorithm
    timer = [0]
    disc = {}
    low = {}
    parent = {}
    bridge_nodes = set()

    def dfs(u):
        disc[u] = low[u] = timer[0]
        timer[0] += 1
        for v in adj.get(u, set()):
            if v not in disc:
                parent[v] = u
                dfs(v)
                low[u] = min(low[u], low[v])
                if low[v] > disc[u]:
                    bridge_nodes.add(u)
                    bridge_nodes.add(v)
            elif v != parent.get(u):
                low[u] = min(low[u], disc[v])

    for n in nodes_list:
        if n not in disc:
            parent[n] = -1
            dfs(n)

    if not bridge_nodes:
        print("No bridge nodes found — graph is well-connected!")
        conn.close()
        return

    print(f"\nBridge nodes ({len(bridge_nodes)} — removing these disconnects the graph):\n")
    for nid in sorted(bridge_nodes):
        row = conn.execute("SELECT name, path FROM nodes WHERE id=?", (nid,)).fetchone()
        degree = len(adj.get(nid, set()))
        display = row[0].replace("-", " ").title()
        print(f"  ⚠ {display}  ({row[1]})  [{degree} connections]")
    conn.close()


def orphans():
    """Find notes with zero wikilinks in or out."""
    conn = get_graph()

    connected = set()
    for s, t in conn.execute("SELECT source_id, target_id FROM edges").fetchall():
        connected.add(s)
        connected.add(t)

    all_nodes = conn.execute("SELECT id, name, path FROM nodes").fetchall()
    orphan_list = [(n, p) for (i, n, p) in all_nodes if i not in connected]

    if not orphan_list:
        print("No orphans! Every note has at least one connection.")
        conn.close()
        return

    print(f"\nOrphan notes ({len(orphan_list)} — no wikilinks in or out):\n")
    for name, path in sorted(orphan_list):
        display = name.replace("-", " ").title()
        print(f"  ○ {display}  ({path})")
    conn.close()


def spreading_activation(query, top_n=10, decay=0.5):
    """
    Spreading activation: start from a concept, propagate through graph.
    
    Like Physarum polycephalum finding optimal paths:
    1. Seed nodes get activation 1.0
    2. Each hop multiplies by decay factor
    3. Nodes with multiple incoming paths accumulate activation
    4. Return top-N activated nodes
    """
    conn = get_graph()

    # Find seed node
    seed = find_node(conn, query)
    if not seed:
        print(f"Node not found: {query}")
        return

    # Build adjacency (undirected)
    adj = {}
    for s, t in conn.execute("SELECT source_id, target_id FROM edges").fetchall():
        adj.setdefault(s, set()).add(t)
        adj.setdefault(t, set()).add(s)

    # Spreading activation (BFS-based, bounded)
    activation = {}
    visited_depth = {seed[0]: 0}
    queue = deque([(seed[0], 1.0, 0)])
    max_depth = 5

    while queue:
        node, act, depth = queue.popleft()
        if depth >= max_depth:
            continue

        new_act = act * decay
        if new_act < 0.01:
            continue

        for neighbor in adj.get(node, set()):
            # Accumulate activation
            activation[neighbor] = activation.get(neighbor, 0) + new_act

            # Only traverse each node once (BFS guarantee)
            if neighbor not in visited_depth:
                visited_depth[neighbor] = depth + 1
                queue.append((neighbor, new_act, depth + 1))

    # Remove seed from results
    del activation[seed[0]]

    # Sort by activation score
    ranked = sorted(activation.items(), key=lambda x: -x[1])[:top_n]

    seed_display = seed[1].replace("-", " ").title()
    print(f"\nSpreading activation from: {seed_display}\n")
    print(f"  {'Note':<45} {'Score':>8}  {'Hops':>4}")
    print(f"  {'─'*45} {'─'*8}  {'─'*4}")

    for nid, score in ranked:
        row = conn.execute("SELECT name, path FROM nodes WHERE id=?", (nid,)).fetchone()
        display = row[0].replace("-", " ").title()
        hops = visited_depth.get(nid, "?")
        bar = "█" * int(score * 20)
        print(f"  {display:<45} {score:>8.3f}  {hops:>4}  {bar}")

    conn.close()


def stats():
    """Show graph statistics."""
    conn = get_graph()
    try:
        nodes = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        edges = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        last_build = conn.execute("SELECT value FROM meta WHERE key='last_build'").fetchone()

        # Degree distribution
        adj = {}
        for s, t in conn.execute("SELECT source_id, target_id FROM edges").fetchall():
            adj.setdefault(s, set()).add(t)
            adj.setdefault(t, set()).add(s)

        degrees = [len(adj.get(nid, set())) for nid, in conn.execute("SELECT id FROM nodes").fetchall()]
        connected = [d for d in degrees if d > 0]
        orphan_count = degrees.count(0)

        print(f"\nqmd-graph stats\n")
        print(f"  Nodes:      {nodes}")
        print(f"  Edges:      {edges}")
        print(f"  Orphans:    {orphan_count}")
        print(f"  Connected:  {len(connected)}")
        if connected:
            avg_deg = sum(connected) / len(connected)
            max_deg = max(connected)
            print(f"  Avg degree: {avg_deg:.1f}")
            print(f"  Max degree: {max_deg}")

            # Find most connected
            top = sorted(
                [(nid, len(adj.get(nid, set()))) for nid, in conn.execute("SELECT id FROM nodes").fetchall()],
                key=lambda x: -x[1]
            )[:5]
            print(f"\n  Most connected:")
            for nid, deg in top:
                row = conn.execute("SELECT name, path FROM nodes WHERE id=?", (nid,)).fetchone()
                display = row[0].replace("-", " ").title()
                print(f"    {display} ({deg} connections)")

        if last_build:
            print(f"\n  Last build: {last_build[0]}")
    finally:
        conn.close()


# --- CLI ---

def main():
    parser = argparse.ArgumentParser(
        description="qmd-graph: wikilink-aware graph layer for qmd",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s build                          Build graph from qmd index
  %(prog)s build -c brain                 Build from brain collection only
  %(prog)s path "Misha" "Acoustics"       Shortest path between notes
  %(prog)s related "Entrainment" -d 3     All notes within 3 hops
  %(prog)s activate "Golden Circle"       Spreading activation
  %(prog)s bridges                        Find critical bridge notes
  %(prog)s orphans                        Find disconnected notes
  %(prog)s stats                          Graph statistics
        """
    )
    sub = parser.add_subparsers(dest="command")

    # build
    p_build = sub.add_parser("build", help="Build graph from qmd index")
    p_build.add_argument("-c", "--collection", help="Limit to collection")
    p_build.add_argument("-v", "--verbose", action="store_true")

    # path
    p_path = sub.add_parser("path", help="Shortest path between two notes")
    p_path.add_argument("start", help="Start note")
    p_path.add_argument("end", help="End note")

    # related
    p_rel = sub.add_parser("related", help="Notes within N hops")
    p_rel.add_argument("query", help="Note name")
    p_rel.add_argument("-d", "--depth", type=int, default=2)

    # activate
    p_act = sub.add_parser("activate", help="Spreading activation from a note")
    p_act.add_argument("query", help="Seed note")
    p_act.add_argument("-n", "--top", type=int, default=10)
    p_act.add_argument("--decay", type=float, default=0.5)

    # bridges
    sub.add_parser("bridges", help="Find bridge notes")

    # orphans
    sub.add_parser("orphans", help="Find disconnected notes")

    # stats
    sub.add_parser("stats", help="Graph statistics")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    if args.command == "build":
        nodes, edges = build_graph(
            collection=args.collection,
            verbose=True
        )
    elif args.command == "path":
        shortest_path(args.start, args.end)
    elif args.command == "related":
        related(args.query, depth=args.depth)
    elif args.command == "activate":
        spreading_activation(args.query, top_n=args.top, decay=args.decay)
    elif args.command == "bridges":
        bridges()
    elif args.command == "orphans":
        orphans()
    elif args.command == "stats":
        stats()


if __name__ == "__main__":
    main()
