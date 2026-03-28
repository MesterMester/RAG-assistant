# Graph Views Specification

## Goal

The assistant should evolve from having a single mindmap view into a graph-based view system with two primary perspectives over the same underlying records:

- `Context Graph`
- `Execution Graph`

Later, the same model may support a third perspective:

- `Global Graph`

The key principle is:

- records are shared
- views are different projections
- context and planning must not be forced into the same graph structure

## Why Two Main Graph Views

The current mindmap is strongest as a structural and relational view.
It answers:

- what belongs together
- what depends on what
- what is related to a case, project, decision, or note

But planning needs a different question:

- when do I want to work on this
- what is in current focus
- what gets postponed to the next work block, week, or later period

This leads to two distinct but connected graph views.

## 1. Context Graph

### Purpose

The `Context Graph` is the logical knowledge map of the work domain.

It is best for:

- case understanding
- relation editing
- hierarchy editing
- decision context
- navigating linked information

### Primary Node Types

- `organization`
- `team`
- `project`
- `case`
- `task`
- `decision`
- `person`
- `event`
- `note`
- `source_item`

### Main Questions It Answers

- what is this task part of
- what information supports this case
- what other notes, people, and decisions are connected
- what is blocked by what

### Main Interactions

- drag node to change hierarchical parent
- create or remove relation links
- open record detail from a node
- expand around a selected record
- switch to filtered subgraphs such as one project or one case

## 2. Execution Graph

### Purpose

The `Execution Graph` is the planning and scheduling map.

It is not a classic calendar and not a plain timeline.
It is a graph-like planning surface where `task` nodes are placed into time-oriented or focus-oriented containers.

It is best for:

- planning current work
- shifting unfinished tasks
- seeing near-term workload
- separating active focus from backlog
- reviewing weekly and multi-week intent

### Dominant Node Types

- `task`
- optionally `event`
- optionally `decision`
- optionally `note` when a note is part of current focus

### Planning Containers

The first version should use fixed planning containers.

Recommended MVP containers:

- `main_focus`
- `today`
- `tomorrow`
- `this_week`
- `next_week`
- `later`
- `parking`
- `planned_unassigned`

Second-stage containers can become more granular:

- `monday_am`
- `monday_pm`
- `tuesday_am`
- `tuesday_pm`
- `week_plus_1`
- `week_plus_2`
- `this_month`
- `next_month`
- `next_quarter`

### Main Questions It Answers

- what am I actively working on now
- what is planned for the next work block
- what slipped and needs replanning
- what is intentionally parked

### Main Interactions

- drag task between planning containers
- drag task into `main_focus`
- drag task out of focus into a later bucket
- optionally reorder tasks inside a container
- open record detail from a node
- jump from a task into the Context Graph

## 3. Future Global Graph

### Purpose

The `Global Graph` is an Obsidian-like graph view over the whole RAG space.

It is best for:

- discovery
- navigation
- cluster detection
- finding isolated or under-linked records

This should come after the two primary graphs are stable.

## Shared Record Principle

The same `KnowledgeRecord` should appear in multiple views without duplication.

Example:

- a task belongs to a case in the `Context Graph`
- the same task is placed under `next_week` in the `Execution Graph`

This means:

- logical context placement and planning placement are separate concepts
- neither should overwrite the other

## Required Model Separation

The system currently has:

- `parent_id` for primary hierarchy
- `relations` for additional links

That is enough for the `Context Graph`, but not enough for the `Execution Graph`.

The next modeling step should separate:

- `logical relations`
- `planning placement`

### Recommended Minimal Extension

Keep the current fields for compatibility, and add planning-specific metadata.

Recommended additions to `KnowledgeRecord`:

- `planning_bucket: str`
- `planning_order: int | None`
- `focus_rank: int | None`

Meaning:

- `planning_bucket` stores where the record is placed in the `Execution Graph`
- `planning_order` stores order inside a container when needed
- `focus_rank` allows a compact prioritized focus area

Recommended initial default:

- empty string means not scheduled in the `Execution Graph`

### Optional Future Extension

If the execution model becomes more advanced later, planning placement can move into its own structure, for example a separate placement table or JSON file.

That would allow:

- multiple planning layers
- historical rescheduling
- multiple simultaneous boards

But this is not needed for the first implementation.

## Relation Model Direction

The current `relations: list[str]` is intentionally simple, but too limited for a richer graph system.

The next relation step should move toward typed edges.

Recommended direction:

- `parent`
- `related_to`
- `depends_on`
- `blocks`
- `decides`
- `evidence_for`
- `references`

This can start as a backward-compatible extension later.
It is not required for the first `Execution Graph` MVP.

## Drag And Drop Semantics

Drag-and-drop should become a first-class interaction in both graph views.

### In Context Graph

Dragging means:

- changing parent
- changing local structure
- later editing or creating links

### In Execution Graph

Dragging means:

- rescheduling
- reprioritizing
- moving into or out of focus
- parking and un-parking work

This is important because a drag action in the execution surface is not just visual.
It is a planning decision and should persist immediately.

## Suggested UX Rules

- Context Graph should default to filtered views, not the entire universe
- Execution Graph should default to a compact planning surface
- Both graphs should allow opening the same record detail panel
- Both graphs should allow fast jump to the other graph for the selected record
- The user should always feel they are manipulating the same object from different perspectives

## Recommended Implementation Order

### Phase 1

- preserve current mindmap as the first `Context Graph`
- rename mentally and in docs from generic mindmap to context-oriented graph view
- add `planning_bucket` to the shared record model
- define the MVP planning containers
- use `docs/phase-1-implementation-plan.md` as the concrete implementation guide

### Phase 2

- build `Execution Graph` MVP with fixed buckets
- support drag-and-drop between buckets
- persist planning placement changes immediately

### Phase 3

- add `main_focus` area with strong visual priority
- allow simple ordering inside a planning bucket
- add filtered context jump from execution nodes

### Phase 4

- improve Context Graph drag-and-drop for hierarchy editing
- introduce typed relations
- add suggestions for missing links

### Phase 5

- build `Global Graph`
- add graph filters, clustering, and discovery workflows

## MVP Success Criteria

The first successful version should make these workflows easy:

1. create a task in a case
2. place the same task into `main_focus` or `this_week`
3. drag the task to `next_week` if it slips
4. open the same task in the `Context Graph` and still see its case relations
5. return to the `Execution Graph` without losing placement

If this works cleanly, the architecture is on the right path.
