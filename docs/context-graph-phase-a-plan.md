# Context Graph Phase A Plan

## Goal

Phase A is the first implementation step toward `Context Graph 2.0`.

Its job is not to deliver the full final graph editor.
Its job is to replace the current static SVG-based context mindmap with a first custom graph component that is:

- filterable
- selectable
- visually clearer
- ready for inline editing

Phase A should create a stable base for later:

- drag-and-drop relation editing
- typed edges
- full graph mode
- richer layouts

## Phase A Outcome

At the end of Phase A, the project should have:

1. a custom `Context Graph` component instead of the current Graphviz SVG renderer
2. a `Mindmap` visual mode as the first supported layout mode
3. graph-side filtering before render
4. node click selection wired back into Streamlit
5. a side-panel-style editor next to the graph
6. visual distinction between hierarchy edges and secondary relations

Not yet required in Phase A:

- true on-canvas relation creation
- edge editing
- multi-parent editing on-canvas
- separate `Graph` mode layout
- persistent manual node positions

## Why This Phase Exists

The current context graph is generated in [streamlit_app.py](/home/attila/Programs/AI/GitHUB1/RAG-asszisztens/src/rag_assistant/streamlit_app.py) by:

- `build_mindmap_lines()`
- `render_mindmap_svg()`
- `render_interactive_mindmap()`

This is enough for static visualization, but it is already the limiting factor for:

- rich filtering
- on-canvas interaction
- smoother graph styling
- future edge editing

Phase A should therefore replace the renderer first, not redesign the whole data model immediately.

## Scope

### Included

- new custom `context_graph_component`
- first-pass graph payload builder in Python
- filtering controls for the context graph
- click-to-select graph nodes
- inline edit panel next to the graph
- hierarchy edge rendering
- secondary relation rendering from existing `relations`

### Excluded

- new persistent edge storage
- custom edge creation by drag
- edge delete from canvas
- graph mode with force layout
- saveable node coordinates
- relation type editor

## Technical Decision

Phase A should use a custom Streamlit component with Cytoscape.

Reason:

- Phase A already benefits from better rendering and event handling
- Phase B can extend the same component instead of replacing it again
- the component can start in a simple mode and grow incrementally

## Architecture

### Python Side Responsibilities

Python should:

- collect records
- apply UI filters
- convert records into graph nodes
- convert hierarchy and current simple relations into graph edges
- pass selected node id into the component
- receive interaction events from the component
- persist record edits using existing save flows

### Component Responsibilities

The component should:

- render nodes and edges with Cytoscape
- support zoom / pan / selection
- highlight current selection
- emit click events
- switch layout between a calm mindmap-like layout and future-ready structure

## Phase A UI Structure

The `Context Graph` tab should be split into two columns:

- left: filters and graph options
- right: graph surface

Or, if the current tab structure feels better:

- top: filter bar
- middle: graph component
- bottom or side: selected record editor

Preferred direction:

- graph on the left or center
- selected-node editor on the right

The key principle is:

- selection and editing happen in one place
- not in a separate distant view

## Filter Set For Phase A

Phase A should support these filters in the Context Graph tab:

- `entity_type`
- `status`
- `project`
- `case`
- `organization`
- `team`
- `active only`
- `due_at from`
- `due_at to`
- `show relations`
- `show only hierarchy`

Optional if low effort:

- text search
- tag filter

## Data Projection For Phase A

Phase A should still work with the current record model.

### Node Source

Each `KnowledgeRecord` becomes one graph node.

Node payload should include:

- `id`
- `label`
- `entity_type`
- `status`
- `project`
- `case_name`
- `due_at`
- `next_step`
- `selected`

### Edge Source

Two edge families are needed.

#### Hierarchy Edges

From:

- `parent_id`

Rendered as:

- primary, solid, calmer lines

#### Secondary Relation Edges

From:

- current `relations: list[str]`

Rendered as:

- lighter or dashed lines

Important note:

The current `relations` field is not ideal.
In Phase A we should still support it as-is, so no migration is needed yet.

## Payload Contract

Recommended component payload shape:

```python
{
    "mode": "mindmap",
    "selected_node_id": "...",
    "nodes": [...],
    "edges": [...],
    "filters": {...},
    "options": {
        "show_relations": True,
        "show_only_hierarchy": False,
    },
}
```

Recommended event outputs from the component:

- `{"action": "select_node", "record_id": "..."}`
- later:
  - `{"action": "create_edge", ...}`
  - `{"action": "delete_edge", ...}`
  - `{"action": "move_node", ...}`

Phase A only requires:

- `select_node`

## Streamlit File-Level Work

### 1. New Component Wrapper

Add:

- [context_graph_component.py](/home/attila/Programs/AI/GitHUB1/RAG-asszisztens/src/rag_assistant/context_graph_component.py)

Responsibility:

- declare the Streamlit custom component
- expose `context_graph(...)`

### 2. New Frontend Component Folder

Add:

- [context_graph_component](/home/attila/Programs/AI/GitHUB1/RAG-asszisztens/src/rag_assistant/context_graph_component)
- [index.html](/home/attila/Programs/AI/GitHUB1/RAG-asszisztens/src/rag_assistant/context_graph_component/index.html)

Responsibility:

- host Cytoscape
- render graph
- emit node selection events

### 3. Streamlit Integration

Update [streamlit_app.py](/home/attila/Programs/AI/GitHUB1/RAG-asszisztens/src/rag_assistant/streamlit_app.py):

- add graph payload builder helpers
- add Context Graph filter controls
- replace the current SVG rendering path in the tab
- keep the selected record editor in the same tab

### 4. Optional Helper Module

If the payload logic grows, create:

- `src/rag_assistant/context_graph_payload.py`

Responsibility:

- filtered node building
- edge building
- view-mode payload construction

This is optional in Phase A.
If changes stay small, the logic can remain in `streamlit_app.py` first.

## Exact Replacement Strategy

### Current Code To Replace Later In Tab Rendering

Current functions:

- `build_mindmap_lines()`
- `render_mindmap_svg()`
- `render_interactive_mindmap()`

Phase A recommendation:

- keep them temporarily during rollout
- add the new component alongside them behind a toggle if helpful
- once stable, make Cytoscape the default path

This lowers rollout risk.

## Phase A Interaction Model

### Supported

- zoom
- pan
- click node
- selected node highlight
- click-to-open record in side editor

### Not Yet Supported

- drag node to reparent
- drag edge handles
- on-canvas delete edge

This is deliberate.
Phase A should stabilize the rendering and selection loop first.

## Editing Model In Phase A

The selected-node side panel should let the user edit:

- title
- summary
- status
- project
- case
- due date
- next step
- parent
- simple relations

This can reuse existing editing helpers already present in the app.

That means:

- no new persistence pathway is needed
- use the same `update_record()` and save flow already used elsewhere

## Visual Design Direction

Phase A should visibly improve clarity over the current Graphviz output.

Recommended:

- node shape by entity type
- warm neutral background like the execution board
- softer curved hierarchy lines
- dimmed secondary relations
- selected node ring
- hovered node highlight
- mini legend for colors and shapes

The goal is:

- calmer
- clearer
- more intentional

not:

- maximal visual density

## Acceptance Criteria

Phase A is done when:

1. the `Context Graph` tab renders through the custom component
2. the graph can be filtered by at least `entity_type`, `status`, and `due_at` range
3. clicking a node selects the corresponding record
4. the selected record is editable in the same tab
5. hierarchy and secondary relations are visually different
6. the current manual record persistence still works
7. old records need no migration to be rendered

## Risks

### 1. Frontend Weight

The biggest new dependency is the Cytoscape frontend bundle.

Mitigation:

- keep Phase A feature scope narrow
- avoid extra plugins unless needed

### 2. Event Complexity

Streamlit custom components can become noisy if too many interaction events fire.

Mitigation:

- Phase A only sends selection events
- avoid node-position persistence yet

### 3. Data Model Ambiguity

Current `relations` are too simple for long-term graph editing.

Mitigation:

- Phase A only visualizes them
- Phase B introduces typed edges

## Implementation Order

### Step 1

Create the component wrapper and the minimal Cytoscape HTML shell.

### Step 2

Build Python payload helpers for nodes and edges.

### Step 3

Wire the component into the `Context Graph` tab with selection only.

### Step 4

Add filters and pass filtered nodes/edges into the component.

### Step 5

Attach the existing record editor to graph selection in the same tab.

### Step 6

Tune visual styling and legend.

## Phase B Preview

After Phase A is stable, Phase B should introduce:

- typed edges
- edge create / delete
- graph mode next to mindmap mode
- stronger in-canvas editing

That is where the Context Graph becomes a real visual graph editor.
