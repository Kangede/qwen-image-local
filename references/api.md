# Qwen-Image-2.1 API and CLI reference

Read this file when choosing request parameters, validating a resolution, diagnosing connection discovery, or constructing a CLI call.

## Endpoints

- Availability: `GET /v1/models`
- Text to image: `POST /v1/images/generations` with JSON
- Image editing: `POST /v1/images/edits` with multipart form data and repeated `image[]` files

The CLI always checks `/v1/models` before a live generation/edit request and requires the selected alias to be present.

## Connection discovery

Priority is:

1. Explicit `--base-url` plus `--api-key-env`.
2. `QWEN_IMAGE_BASE_URL` and `QWEN_IMAGE_API_KEY` overrides.
3. The active provider in `${CODEX_HOME:-~/.codex}/config.toml`; the CLI reads its `base_url` and the environment-variable name in `env_key`.
4. OpenAI-compatible or Anthropic-compatible session environment variables listed in `config/defaults.json`.

`ANTHROPIC_BASE_URL` may point at the proxy root. Other addresses may already end in `/v1`; the CLI normalizes both forms. It never defaults to the public OpenAI API and never prints a secret.

If automatic discovery fails, set, for example:

```bash
export QWEN_IMAGE_BASE_URL="http://proxy-host:8090/v1"
export QWEN_IMAGE_API_KEY="..."
```

Do not place the key literal in `--api-key-env`; that argument accepts the environment variable's name.

## Fixed request invariants

| Field | Behavior |
| --- | --- |
| `model` | Defaults to `Qwen-Image-2.1` from `config/defaults.json`; override with `--model` or `QWEN_IMAGE_MODEL`. |
| `n` | Always `1`. Generate additional images with additional requests. |
| `num_inference_steps` | Always `40`. |
| `response_format` | Always `b64_json`. |
| `size` | Defaults to `1024x1024`; must pass the resolution rules below. |
| `generator_device` | Fixed to `cpu` for deterministic request-side random-number generation. |
| `seed` | Defaults to `42`; may be changed to a non-negative 32-bit integer. For edits, follow the seed policy below. |
| `guidance_scale` | Defaults to `1`, which disables CFG. A value greater than `1` requires `--negative-prompt`. |
| `output_format` | `png` or `jpeg`; `jpg` is normalized to `jpeg`. |
| `background` | `auto` or `transparent`. Transparent output requires PNG. |
| `enable_cache_dit` | Defaults to `false`. Enable only when explicitly requested and after accepting that caching can alter numerical output and needs quality evaluation. |

JPEG may use `--output-compression 0..100`. PNG must not set compression through this option.

### Seed policy for edits

When editing or iteratively adjusting an image, choose a fresh non-negative 32-bit seed that differs from every known seed used to produce the current reference image or images, and pass it explicitly with `--seed`. If the reference image came from an external source and its seed is unknown, choose any fresh seed. Do not reuse a generation seed for an edit because it can add noise or blur to otherwise unchanged areas. Reuse the same seed only when the user explicitly requests it.

## Supported aspect ratios and upper bounds

Dimensions must be positive multiples of 32. They may be lower than the listed values, but never higher in either dimension for the matched aspect-ratio family.

| Ratio | Maximum |
| --- | ---: |
| `1:1` | `2048x2048` |
| `4:3` | `2400x1792` |
| `3:4` | `1792x2400` |
| `3:2` | `2528x1696` |
| `2:3` | `1696x2528` |
| `16:9` | `2752x1536` |
| `9:16` | `1536x2752` |

The official maximum dimensions are rounded to model-friendly multiples, so the CLI matches the nearest supported ratio with a small tolerance stored in `config/defaults.json`.

Prefer the lowest adequate resolution. Examples:

- Square draft or normal asset: `1024x1024`
- Landscape 4:3: `1024x768`, `1536x1152`, or up to `2400x1792`
- Portrait 3:4: `768x1024`, `1152x1536`, or up to `1792x2400`
- Landscape 3:2: `1536x1024` or up to `2528x1696`
- Portrait 2:3: `1024x1536` or up to `1696x2528`
- Widescreen 16:9: `1344x768`, `1792x1024`, or up to `2752x1536`
- Vertical 9:16: `768x1344`, `1024x1792`, or up to `1536x2752`

The examples are still validated; rounding must remain within the configured ratio tolerance.

## Transparent PNG

Transparency requires all three conditions:

1. `--background transparent`
2. `--output-format png`
3. A prompt that explicitly asks for an RGBA image, an alpha channel, and a transparent background

The CLI adds the official transparency framing if it is missing. `background=transparent` alone does not condition the model to remove the background. JPEG cannot preserve alpha.

PNG references keep their alpha channel during editing. RGB/JPEG references are treated as opaque.

## Editing

- Use one to five `--image` arguments in meaningful order.
- Refer to them in the prompt as `Picture 1`, `Picture 2`, etc.
- Repeat preservation constraints: what may change and what must stay unchanged.
- For iterative editing, send the previous output as a new reference and use a different seed from the one that produced that reference; the server keeps no editing conversation state.
- Reference PNG and JPEG files are supported by the CLI. Each file is capped at the byte limit in `config/defaults.json` before upload.

## CLI examples

Availability only:

```bash
python3 scripts/qwen_image.py check
```

Generation:

```bash
python3 scripts/qwen_image.py generate \
  --prompt "A capybara reading a book by candlelight" \
  --size 1024x1024 \
  --seed 42 \
  --output-format png \
  --out output/qwen-image/capybara.png
```

Transparent PNG:

```bash
python3 scripts/qwen_image.py generate \
  --prompt "A single fluffy orange cat, full body, clean cutout" \
  --background transparent \
  --output-format png \
  --out output/qwen-image/cat-cutout.png
```

Editing with ordered references:

```bash
python3 scripts/qwen_image.py edit \
  --image /path/to/subject.png \
  --image /path/to/style-reference.jpg \
  --prompt "Use Picture 1 as the subject and Picture 2 only as the visual style reference. Preserve Picture 1's identity and pose." \
  --seed 43 \
  --out output/qwen-image/styled-subject.png
```

Dry-run validation performs no network call:

```bash
python3 scripts/qwen_image.py --base-url http://127.0.0.1:8090/v1 --dry-run generate \
  --prompt "Validation only" --size 1024x1024
```

## Maintenance sources

- Qwen-Image-2.1 model card and native aspect ratios: <https://modelscope.cn/models/Qwen/Qwen-Image-2.1>
