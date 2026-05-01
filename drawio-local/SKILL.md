---
name: drawio-local
description: Use this skill when the user wants to create or update flowcharts, architecture diagrams, swimlanes, org charts, sequence diagrams, mind maps, or native `.drawio` files on this machine.
x-custom-skill: true
x-managed-by: cc-switch
x-source-repo: dmdmwshr/custom-skills
x-edit-policy: edit-source-repo-only
---

# drawio-local

Use this skill when the user wants to create or update flowcharts, architecture diagrams, swimlanes, org charts, sequence diagrams, mind maps, or native `.drawio` files on this machine.

## Goal

Produce editable local draw.io artifacts first, then optionally export them to `png`, `svg`, or `pdf`.

## Default Workflow

1. Prefer generating a native `.drawio` XML file in the current workspace unless the user asks for Mermaid only.
2. Ask as little as possible. If the diagram type is obvious, proceed directly.
3. Save source files with descriptive names such as `system-flow.drawio` or `approval-process.drawio`.
4. If draw.io Desktop or a local draw.io container is available, offer export to `png`, `svg`, or `pdf`.
5. When the user wants AI-direct manipulation, prefer the local Codex MCP server `drawio` if it is available in the environment. Otherwise generate the `.drawio` file yourself.

## Authoring Rules

- Use clear top-down or left-to-right layouts.
- Keep labels short and readable.
- Use standard flowchart semantics unless the user requests a different notation.
- When relationships are simple, avoid excessive styling and preserve editability.
- If the user asks for a process diagram from prose, extract steps, decision points, inputs, outputs, and actors before generating the file.

## File Conventions

- Default output directory: current workspace root, or a user-specified folder.
- Default source extension: `.drawio`
- Optional exports: `.png`, `.svg`, `.pdf`

## Helpful Patterns

- For quick drafts, Mermaid is acceptable as an intermediate representation.
- For final editable delivery, prefer native draw.io XML content.
- When exporting is blocked by missing desktop tooling, still generate the `.drawio` source so the user can open and refine it locally.
