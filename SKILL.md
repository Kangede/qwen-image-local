---
name: qwen-image-local
description: Generate or edit raster images with Qwen-Image-2.1 through the current session's authenticated OpenAI-compatible Images API when the active agent model is Qwen, GLM, or another non-GPT model. Do not use in GPT-family Codex sessions; use the built-in imagegen skill there. Supports text-to-image, up to five reference images, native PNG transparency, and project-local output files.
---

# Qwen Image Local

Use the current session's authenticated OpenAI-compatible Images API to generate or edit bitmap images with Qwen-Image-2.1.

## Routing boundary

Apply this boundary before doing anything else:

- If the active agent model is in the GPT family, stop this workflow and use Codex's built-in `imagegen` skill and native `image_gen` tool.
- Use this skill only when the active model is Qwen, GLM, or another clearly non-GPT model.
- Explicit invocation does not waive the model-family boundary. If the active model family cannot be established from the session or provider configuration, inspect that configuration; if it remains ambiguous, ask the user instead of competing with `imagegen`.
- Do not silently fall back between Qwen-Image and a GPT Image model.

## Execution mode and Codex presentation

This is a CLI-backed Skill. A standalone Skill cannot emit Codex's private `generatedImage` result type, so it cannot reproduce the built-in tool's blank generation canvas or native image-adjustment surface.

In Codex App:

1. Before starting the command, send a short progress update saying what is being generated or edited.
2. Run `scripts/qwen_image.py`; do not write a one-off API client.
3. Inspect the saved image with `view_image` when available.
4. Open the final file with the Codex file viewer when `open_in_codex` is available, and embed the absolute local path in the final response so the image is clickable.
5. For follow-up adjustments, call this Skill's `edit` mode with the previously generated file as a reference image. Do not claim that the file viewer itself is a native image editor.

In other agents or headless environments, report the absolute saved path and use the same CLI for later edits.

## Fixed safety and quality constraints

- Send exactly one output per request: `n=1`. For multiple requested images, make separate requests, preferably sequentially.
- Keep `num_inference_steps=40`.
- Keep `response_format=b64_json`.
- Default to `size=1024x1024`.
- Output format is `png` or `jpeg` only (`jpg` is accepted by the CLI as an alias for `jpeg`).
- Native transparency requires PNG, `background=transparent`, and an explicit transparency instruction in the prompt. JPEG cannot retain alpha.
- Width and height must be positive multiples of 32, match one of the supported aspect-ratio families, and never exceed that family's official maximum dimensions. The CLI validates this before any network request.
- Image editing accepts at most five ordered reference images, even though the model itself can accept more.
- The default model alias is stored in `config/defaults.json` and may be overridden without editing the script.
- Never print, log, or place an API key in command arguments. Keys must come from the current provider's environment variable or another supported environment variable.

Read [references/api.md](references/api.md) when choosing API parameters, sizes, connection discovery, or CLI flags. Read [references/prompting.md](references/prompting.md) when shaping a generation/edit prompt or preserving edit invariants.

## Workflow

1. Enforce the model-family routing boundary above.
2. Decide whether the request is a new generation or an edit:
   - No input image: `generate`.
   - Preserve or change an existing image, or combine references: `edit`.
3. Resolve every reference image to a readable local file. Label their roles in order as `Picture 1`, `Picture 2`, and so on. Never send more than five.
4. Shape a concise production prompt using `references/prompting.md`. Preserve exact requested text verbatim and repeat edit invariants.
5. Choose the smallest resolution that meets the user's intended use. Stay at `1024x1024` unless another aspect ratio or resolution is materially useful.
6. Choose a semantic project-local output path under `output/qwen-image/` unless the user requested another destination. Do not overwrite an existing file unless the user explicitly requested replacement.
7. Run the bundled CLI. It automatically discovers the current session's proxy address and API-key environment where possible, calls `/v1/models` first, and refuses generation/editing unless the configured model alias is available.
8. If discovery fails, ask the user to set the relevant address/key environment variables locally. Never ask them to paste a full key into chat.
9. If the availability check fails, stop. Do not substitute a different endpoint or repeatedly retry an unavailable model.
10. Inspect the result, compare it with the subject/style/composition/text/invariant requirements, and make only targeted edit iterations.
11. Report the saved path, final prompt, size, seed, format, and whether the operation was generation or editing.

## Command shape

Use the Skill directory shown in the loaded Skill path. Typical commands are:

```bash
python3 <skill-dir>/scripts/qwen_image.py generate \
  --prompt "A capybara reading a book by candlelight" \
  --out output/qwen-image/capybara.png
```

```bash
python3 <skill-dir>/scripts/qwen_image.py edit \
  --image /absolute/path/to/input.png \
  --prompt "Change only the red teapot to blue; preserve the table, window, composition, and lighting." \
  --out output/qwen-image/blue-teapot.png
```

Use `--base-url` only when session discovery is unavailable. Use `--api-key-env NAME` to name an environment variable; never pass the key value itself.
