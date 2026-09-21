# qwen-image-local

Agent Skill for generating and editing images with Qwen-Image-2.1 through the current session's OpenAI-compatible Images API.

The Skill supports text-to-image generation, image editing with up to five references, transparent PNG output, provider configuration discovery, and project-local image files. In GPT-family Codex sessions, use the built-in `imagegen` Skill instead.

See [SKILL.md](SKILL.md) for the complete workflow and [references/api.md](references/api.md) for API parameters and resolution limits.

## Install

Install this repository as a user-level Agent Skill, keeping `SKILL.md`, `agents`, `assets`, `config`, `references`, and `scripts` together. Agents with a Skill installer can install directly from:

```text
https://github.com/Kangede/qwen-image-local
```
