# custom-skills

Personal Codex / cc-switch skills maintained by dmdmwshr.

This repository is the source of truth for self-built skills that are managed through cc-switch.
Official and third-party skills remain managed by cc-switch repository sources.

## Included skills

- bilinote-video-note
- drawio-local
- flclash-proxy-toggle
- mineru-local
- obsidian-notes
- summarize-link-note
- xf-report-filler
- zerox-local

## Management notes

- This repository at `C:\Users\12070\.cc-switch\skills\自建skills` is the source of truth for self-built skills.
- Installed copies under `C:\Users\12070\.cc-switch\skills\<skill-name>` are cc-switch sync output. Do not edit those copies directly.
- Add or update self-built skills in this repository first, then commit and push to `dmdmwshr/custom-skills`.
- After pushing, sync or refresh through cc-switch so the installed root is regenerated from the repository source.
- Each self-built skill should carry `x-custom-skill: true` and `x-source-repo: dmdmwshr/custom-skills` metadata in `SKILL.md`.
- Do not restore or run the old `skill-updater` workflow.
