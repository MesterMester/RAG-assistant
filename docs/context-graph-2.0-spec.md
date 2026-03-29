# Context Graph 2.0 Specification

## Goal

The current context mindmap should evolve into a visual knowledge workspace that supports:

- filtered graph exploration
- mindmap-style focused navigation
- true graph view with multiple incoming and outgoing relations
- inline editing of records and relations
- drag-and-drop interaction directly on the graph surface

The target is not "a nicer SVG".
The target is a dedicated interactive graph component for the shared `KnowledgeRecord` model.

## Key Answer First

Yes: a Cytoscape-based implementation can still support a mindmap-style view.

The important design decision is:

- `Mindmap View` and `Graph View` are two modes over the same records
- the renderer changes layout and interaction rules, not the underlying data model

So the system should not choose between:

- mindmap
- graph

It should support both as sibling views in the same Context Graph surface.

## Why Cytoscape

For this product direction, Cytoscape is a strong fit because it handles:

- real graph structures
- multiple edges per node
- layout switching
- filtering and subgraph rendering
- node and edge styling
- selection and interaction events

It is a better fit than the current custom SVG if we want:

- inline relation editing
- richer edge types
- future Obsidian-like full graph exploration

## What Cytoscape Should Do Here

Cytoscape would not replace the record model or the Python backend.
It would replace the current context graph renderer.

Expected role:

- receive nodes and edges from Streamlit
- render them in different visual modes
- send back interaction events such as:
  - node moved
  - parent changed
  - edge created
  - edge removed
  - node opened
  - node edited
  - filter focus changed

## Two Visual Modes

### 1. Mindmap View

Purpose:

- focused understanding
- hierarchical navigation
- case or project centered work

Characteristics:

- one selected root node
- descendants expanded outward
- `parent_id` is the main structural spine
- additional relations are visible but secondary
- the layout is directional and calmer than the full graph

Typical use:

- "show this case and everything under it"
- "show this project with related tasks and notes"
- "focus only on active task context"

### 2. Graph View

Purpose:

- relationship discovery
- many-to-many context
- multiple parent-like connections

Characteristics:

- no single required root
- typed edges are first-class
- multiple incoming and outgoing relations are normal
- clusters and cross-links are visible

Typical use:

- "show everything connected to this decision"
- "show all records around this person, project, and case"
- "show under-linked or isolated areas"

## View Switching

The Context Graph should have a view switcher:

- `Mindmap`
- `Graph`

The selected filters should stay active across both views where possible.

Example:

- filter to `active tasks + notes`
- view in `Mindmap`
- switch to `Graph`
- see the same filtered subset with richer edge structure

## Filtering

The graph must be filterable before rendering.

Required first-pass filters:

- `entity_type`
- `status`
- `project`
- `case`
- `organization`
- `team`
- `tag`
- `due_at` date or date range
- `updated_at` recency
- `active only`

Useful view-level toggles:

- show only tasks
- show only active tasks
- show notes and tasks only
- hide archived / done items
- show only selected node neighborhood
- show only hierarchy edges
- show all relation edges

## Record Editing On The Surface

The user should be able to edit directly from the graph view, not on a separate distant page.

Recommended interaction:

- click node -> side panel opens
- side panel shows editable record fields
- changes save back immediately or with one clear save action

Minimum inline-edit scope:

- title
- summary
- status
- project
- case
- tags
- parent
- due date
- next step

Later inline-edit scope:

- full content
- decision context
- relation labels and relation types

## Relation Editing On The Surface

This is one of the most important upgrades.

The user should be able to:

- drag from one node to another to create a relation
- click an existing edge to edit its type
- delete an edge directly from the graph
- distinguish hierarchy from non-hierarchy visually

Recommended first edge types:

- `parent`
- `related_to`
- `depends_on`
- `blocks`
- `supports`
- `decision_for`
- `references`

## Data Model Direction

The current model has:

- `parent_id`
- `relations: list[str]`

That is enough for a basic mindmap, but not enough for a strong editable graph.

### Recommended Direction

Keep `parent_id` for the hierarchy view, but introduce typed edge storage.

Recommended future structure:

```python
graph_edges: list[dict]
```

Each edge item should support:

- `edge_id`
- `source_id`
- `target_id`
- `relation_type`
- `direction`
- `label`
- `created_at`
- `updated_at`

This can initially live:

- inside each record file as a shared edge list, or
- in a separate graph metadata file under `.rag_assistant/`

Preferred medium-term direction:

- separate graph metadata file

Reason:

- edges belong to the graph, not just to one record
- avoids awkward duplication
- easier to update interactively

## Visual Language

The graph should visually distinguish:

- node types
- status
- selected vs related vs dimmed
- hierarchy edges vs relation edges

Recommended baseline:

- node shape by entity type
- node fill color by entity category
- ring or accent by status
- solid line for hierarchy
- curved or dashed lines for relation edges
- edge color by relation type

## Drag-And-Drop Semantics

### In Mindmap View

- drag node onto another node to propose parent change
- optionally drag from a relation handle to create a non-parent edge

### In Graph View

- drag node for layout adjustment
- drag edge handle from source to target to create relation
- dragging should not silently rewrite parent unless the action explicitly means reparenting

This distinction is important:

- moving a node visually is not always a structural edit
- creating a hierarchy change should feel deliberate

## Layout Strategy

The system should support multiple layout modes.

Recommended initial layouts:

- `mindmap`: directional tree-style layout around one root
- `graph`: force-directed or cose-style layout
- `radial`: useful for one-hop exploration around a selected node

This is another reason Cytoscape is a good fit:

- same graph data
- different layouts
- same interaction model

## Scope Of The First Technical Cut

The first implementation should not try to solve everything.

### Phase A

- replace static SVG context graph with a custom Cytoscape component
- render nodes and current hierarchy
- allow node selection
- allow basic filtering
- open inline side panel editor

### Phase B

- add typed edges
- add edge styling
- add graph mode vs mindmap mode switch
- add relation create/edit/delete interactions

### Phase C

- add neighborhood focus mode
- add saved filters
- add full global graph view
- add more advanced layout options

## Technical Architecture

Recommended architecture:

- Python / Streamlit remains the application shell
- a custom Streamlit component hosts Cytoscape
- Python prepares:
  - filtered node set
  - filtered edge set
  - selected node context
- the component returns:
  - user interaction events
  - node positions
  - relation edit actions

This means the bigger technical change is mostly:

- renderer replacement
- graph event wiring
- richer edge model

It does not require replacing the whole app.

## Product Principle

The Context Graph should feel like:

- a visual editor
- a visual navigator
- a visual filter surface

not just:

- a read-only diagram

That is the core direction for Context Graph 2.0.
