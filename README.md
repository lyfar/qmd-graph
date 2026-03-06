# qmd-graph

Wikilink-aware graph layer for [qmd](https://github.com/tobi/qmd). Find the shortest path between ideas through their connections, not just their content similarity.

## The Problem

qmd is excellent at finding documents by content (BM25 + vector search + reranking). But knowledge isn't just documents — it's the connections between them.

When your notes use `[[wikilinks]]`, they form a graph. Two notes might have zero semantic similarity but be deeply connected through a chain of links. Vector search will never find that path. Graph traversal will.

## The Physarum Analogy

[Physarum polycephalum](https://en.wikipedia.org/wiki/Physarum_polycephalum) (slime mold) finds the shortest path through a maze without a brain, without a map, and without seeing the whole maze. It sends out exploratory tendrils in all directions, then strengthens the paths that connect food sources and lets the rest decay.

`qmd-graph activate` does the same thing: start from a concept, propagate activation through wikilinks, and see which distant notes light up brightest. Notes reachable via multiple paths accumulate more activation — just like the slime mold strengthens high-traffic routes.

This is also how human memory works. [Spreading activation](https://en.wikipedia.org/wiki/Spreading_activation) (Collins & Loftus, 1975) describes how activating one concept in a semantic network causes related concepts to "light up" proportional to their connection strength.

## Install

```bash
# Clone
git clone https://github.com/lyfar/qmd-graph.git

# That's it. Zero dependencies beyond Python 3.8+ stdlib.
# Reads from qmd's index.sqlite, writes to ~/.cache/qmd/graph.sqlite
```

## Usage

### Build the graph

```bash
# Build from all qmd collections
python3 qmd-graph.py build

# Build from a specific collection
python3 qmd-graph.py build -c brain
```

### Find shortest path between notes

```bash
$ python3 qmd-graph.py path "Misha" "Coulomb Friction"

Path (3 hops):

  ● Misha  (people/misha.md)
  → Strc Hearing Loss  (notes/strc-hearing-loss.md)
  → Singing Bowl Physics  (notes/singing-bowl-physics.md)
  → Coulomb Friction Model  (notes/coulomb-friction-model.md)
```

This path is invisible to vector search. "Misha" and "Coulomb Friction Model" have zero semantic similarity. But through the graph, they're 3 hops apart.

### Spreading activation

```bash
$ python3 qmd-graph.py activate "Entrainment"

Spreading activation from: Entrainment

  Note                                             Score  Hops
  ───────────────────────────────────────────── ────────  ────
  Touch Grass                                      7.188     2  ██████████████████
  Moc Psychology Adhd                              6.500     1  █████████████████
  Misha                                            6.500     2  █████████████████
  Sound Therapy And Hearing Loss                   6.375     2  ████████████████
  Singing Bowl Physics                             4.438     2  ████████████████
```

Note how "Touch Grass" scores highest — it's reachable via multiple paths (acoustics, psychology, product), so activation accumulates. This is the Physarum effect: heavily-connected routes get stronger signals.

### Explore neighborhood

```bash
$ python3 qmd-graph.py related "Golden Circle" -d 2

Related to: Golden Circle (depth 2)

  · Depth 1:  (direct links)
    StoryBrand SB7, Positioning Ladder, Brand As Gut Feeling...
  ·· Depth 2:  (2 hops away)
    Touch Grass, Lyfar Studio, Creative Brief, SMILE SCRATCH...

  25 connected notes within 2 hops
```

### Find bridge notes

```bash
$ python3 qmd-graph.py bridges
```

Bridge notes are structural weak points — remove them and the graph splits into disconnected components. These are your most important connective notes.

### Find orphans

```bash
$ python3 qmd-graph.py orphans
```

Notes with zero incoming or outgoing wikilinks. Candidates for linking or archiving.

### Graph stats

```bash
$ python3 qmd-graph.py stats

qmd-graph stats

  Nodes:      502
  Edges:      2003
  Orphans:    116
  Connected:  386
  Avg degree: 8.8
  Max degree: 92

  Most connected:
    Touch Grass (92 connections)
    Misha (64 connections)
```

## How It Works

1. **Build**: Reads all documents from qmd's `index.sqlite`, parses `[[wikilinks]]` with regex, and stores the adjacency graph in `graph.sqlite`
2. **Path**: BFS on the undirected graph (wikilinks are treated as bidirectional)
3. **Activate**: BFS with decaying activation scores that accumulate when multiple paths converge on the same node
4. **Related**: Depth-limited BFS returning all nodes within N hops
5. **Bridges**: Tarjan's bridge-finding algorithm on the undirected graph

## Architecture

```
┌─────────────────────────────┐
│   Your Markdown Vault       │
│   with [[wikilinks]]        │
└──────────┬──────────────────┘
           │ indexed by
           ▼
┌─────────────────────────────┐
│   qmd index.sqlite          │  ← content search (BM25, vector)
│   (documents + content)     │
└──────────┬──────────────────┘
           │ parsed by qmd-graph
           ▼
┌─────────────────────────────┐
│   graph.sqlite              │  ← structure search (path, activate)
│   (nodes + edges)           │
└─────────────────────────────┘
```

Zero new dependencies. Reads qmd's existing SQLite database. Writes a separate `graph.sqlite` (~50KB for 500 notes).

## Requirements

- Python 3.8+
- A qmd index (`~/.cache/qmd/index.sqlite`)
- Notes that use `[[wikilinks]]`

## Future Ideas

- Integration as a qmd subcommand (`qmd graph path A B`)
- Weighted edges based on link context (e.g., `[source]` vs `[see-also]`)
- Combined search: vector seeds + graph expansion (GraphRAG-lite)
- Visualization output (mermaid, graphviz)
- Watch mode: rebuild graph on file changes

## License

MIT

## Credits

Inspired by Physarum polycephalum, Collins & Loftus (1975), and the realization that the best search finds connections, not just matches.

Built as an extension for [qmd](https://github.com/tobi/qmd) by Tobi Lütke.
