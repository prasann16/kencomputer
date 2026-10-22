# Skills

A skill is Claude Code's own mechanism (the `Skill` tool): a folder with a `SKILL.md`
playbook plus optional scripts and references, loaded into context only when the task at
hand matches its description. Ken wires this folder in as a local plugin (see
`brains.py`) so every session — chat and task workers alike — can reach for one of these
"when needed," with nothing for you to install or configure.

This is a different mechanism from [`recipes/`](../recipes): a recipe is prose Ken reads
and improvises from to connect a new service (email, an API key); a skill is Claude
Code's own structured playbook format, with bundled scripts it can run directly.

## What's here, and why these two

Vendored verbatim from [anthropics/skills](https://github.com/anthropics/skills)
(commit `dbd4588`, Oct 2026), Apache 2.0, `LICENSE.txt` kept in each folder:

| Skill | What it's for |
| --- | --- |
| [mcp-builder](mcp-builder) | Build a well-designed MCP server when Ken needs to talk to a new external API or service — the same shape as Ken's own tool servers. |
| [webapp-testing](webapp-testing) | Drive and verify a local web app with Playwright — screenshots, console logs, click-throughs — when Ken is building or debugging one (its own `web/`, or something for you). |

Both are general-purpose, Apache-licensed, and square with the kind of work a Ken
installation actually does on your machine: building and fixing software, not just
chatting.

## What's deliberately not here

Anthropic's `document-skills` plugin (PDF, Word, PowerPoint, Excel manipulation) is the
obvious next thing you'd want for a personal assistant — but its skills are
source-available, not open source (their `LICENSE.txt` forbids redistribution outside
Anthropic's own services), so Ken can't vendor or fetch them automatically without you
agreeing to Anthropic's terms yourself. If you want them, run `/plugin marketplace add
anthropics/skills` and `/plugin install document-skills@anthropic-agent-skills` in any
Claude Code session yourself — that's Anthropic's own sanctioned install path.

The rest of that repo's `example-skills` (creative/design tools, Slack GIFs, internal
comms, a skill-authoring meta-skill with its own eval harness) are real and fine, just a
poorer match for a single-user assistant than the two above. Add more here the same way
if you find one worth it — copy the folder, keep its `LICENSE.txt`, add a row above.

## Updating

These are a point-in-time copy, not a live link. To pick up upstream fixes, re-copy the
skill folder from the anthropics/skills repo and note the new commit SHA above.
