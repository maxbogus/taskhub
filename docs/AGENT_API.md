# Agent API — the smallest contract that works

TaskHub is driven over MCP. This page lists the calls an agent needs to start
work, finish it and keep the registry healthy; everything else is optional.

## Connect

```bash
uv run taskhub-mcp          # stdio transport (what MCP clients launch)
TASKHUB_TRANSPORT=http uv run taskhub-mcp   # streamable HTTP on 127.0.0.1:7801
uv run taskhub mcp-config   # write .mcp.json for this checkout
```

## Five calls to start

```text
list_projects()                                  → pick a project
project_audit(root="~/Repositories")             → is anything stale or duplicated?
get_next_item(project_slug="<slug>")             → highest-priority available item
change_status(item_id, "in_progress", actor="agent")
add_note(project_slug="<slug>", content="...", kind="decision")
```

When the caller works inside a repository, pass the directory instead of a slug:
`get_next_item(cwd="/path/to/repo")` resolves the project by its longest
registered root, so an agent does not need to know slugs at all.

## Tools

| Tool | Arguments | Returns |
|---|---|---|
| `list_projects` | — | `[{slug, name, root_path, ...}]` (archived and the `portfolio` pseudo-project are hidden) |
| `get_next_item` | `project_slug?`, `cwd?`, `required_skill?` | `{item, project}` or `{item: null, reason}` |
| `get_context` | `item_id`, `note_limit?` | item, parent, children, blockers, last notes, resource links |
| `change_status` | `item_id`, `status`, `actor?`, `note?` | updated item; writes an audit note |
| `add_note` | `project_slug`, `content`, `work_item_id?`, `kind?` | note (`attempt`, `blocker`, `insight`, `comment`, `decision`, `handoff`, `ledger`) |
| `search_notes` | `project_slug`, `query`, `kind?` | matching notes (FTS5 index when available) |
| `sync_projects` | `root?`, `max_depth?`, `dry_run?`, `archive_duplicates?`, `moves?` | registry report |
| `project_audit` | `root?`, `max_depth?` | stale roots, duplicates, uncovered directories, orphaned work |
| `move_project` | `slug`, `root`, `dry_run?` | re-pointed project (work items stay) |
| `archive_project` | `slug`, `force?`, `restore?` | soft archive; refuses to hide work items without `force` |

## Resources

| URI | Content |
|---|---|
| `project://{slug}/backlog` | the project's work grouped for reading |
| `project://{slug}/notes` | recent notes |
| `project://{slug}/specs` | documents |
| `project://{slug}/claims` | active claim leases |
| `task://{id}` | one task as a document |
| `task://{id}/context` | task with relations and recent notes |

## Rules an agent must keep

1. **One or two tasks at a time.** Finish or hand off before taking more.
2. **Never set a status by hand.** `change_status` writes the audit note; a
   manual status edit leaves a "broken archive" (no `archived_at`).
3. **`done` ≠ `archived`.** Finished work stays visible until archived.
4. **The slug is immutable, the root path moves.** After a rename use
   `move_project`, never re-register under a new slug — that would split the
   work items between two projects.
5. **Silence is a bug.** If a project looks empty, run `project_audit`: a missing
   root is reported as `detached`, not as "no work".
6. **Ask before leaving a trace.** Notes and decisions are cheap; rewriting
   work items is not.
