#!/usr/bin/env python3
"""CLI for Qwen-Image-2.1 through the current OpenAI-compatible Images API."""

from __future__ import annotations

import argparse
import base64
import binascii
from dataclasses import dataclass
import json
import mimetypes
import os
from pathlib import Path
import re
import sys
import time
import tomllib
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen
import uuid


SKILL_DIR = Path(__file__).resolve().parents[1]
DEFAULTS_PATH = SKILL_DIR / "config" / "defaults.json"
TRANSPARENT_PREFIX = "This is an RGBA image with transparency."
TRANSPARENT_SUFFIX = "The image has an alpha channel and the background is transparent."


class CliError(RuntimeError):
    pass


def fail(message: str) -> None:
    raise CliError(message)


def load_defaults() -> dict[str, Any]:
    try:
        data = json.loads(DEFAULTS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read defaults from {DEFAULTS_PATH}: {exc}")
    if not isinstance(data, dict):
        fail("defaults.json must contain an object")
    return data


DEFAULTS = load_defaults()


def read_prompt(prompt: str | None, prompt_file: str | None) -> str:
    if prompt and prompt_file:
        fail("use --prompt or --prompt-file, not both")
    if prompt_file:
        path = Path(prompt_file).expanduser()
        if not path.is_file():
            fail(f"prompt file not found: {path}")
        value = path.read_text(encoding="utf-8").strip()
    else:
        value = (prompt or "").strip()
    if not value:
        fail("prompt must not be empty")
    return value


def normalize_output_format(value: str) -> str:
    value = value.lower()
    if value == "jpg":
        value = "jpeg"
    if value not in {"png", "jpeg"}:
        fail("output format must be png or jpeg")
    return value


def validate_size(value: str) -> tuple[int, int, str]:
    match = re.fullmatch(r"([1-9][0-9]*)x([1-9][0-9]*)", value.lower())
    if not match:
        fail("size must use WIDTHxHEIGHT, for example 1024x1024")
    width, height = int(match.group(1)), int(match.group(2))
    if width % 32 or height % 32:
        fail("width and height must both be positive multiples of 32")

    ratios = DEFAULTS.get("aspect_ratios", {})
    if not isinstance(ratios, dict) or not ratios:
        fail("defaults.json has no aspect ratio definitions")
    actual = width / height
    candidates: list[tuple[float, str, dict[str, Any]]] = []
    for name, raw in ratios.items():
        if not isinstance(raw, dict):
            continue
        expected = float(raw["ratio"])
        relative_error = abs(actual - expected) / expected
        candidates.append((relative_error, str(name), raw))
    if not candidates:
        fail("defaults.json has invalid aspect ratio definitions")
    error, name, matched = min(candidates, key=lambda item: item[0])
    tolerance = float(DEFAULTS.get("aspect_ratio_tolerance", 0.02))
    if error > tolerance:
        allowed = ", ".join(ratios.keys())
        fail(f"size {value} does not match a supported aspect ratio ({allowed})")

    max_width = int(matched["max_width"])
    max_height = int(matched["max_height"])
    if width > max_width or height > max_height:
        fail(
            f"size {value} exceeds the {name} maximum {max_width}x{max_height}; "
            "dimensions may be lower but not higher"
        )
    return width, height, name


def validate_sampling(args: argparse.Namespace, output_format: str) -> None:
    if not 0 <= args.seed <= 0xFFFFFFFF:
        fail("seed must be between 0 and 4294967295")
    if args.guidance_scale < 1:
        fail("guidance scale must be at least 1")
    if args.guidance_scale > 1 and not args.negative_prompt:
        fail("guidance scale greater than 1 requires --negative-prompt")
    if args.negative_prompt and args.guidance_scale <= 1:
        fail("--negative-prompt requires --guidance-scale greater than 1")
    if args.background == "transparent" and output_format != "png":
        fail("transparent background requires PNG output; JPEG cannot preserve alpha")
    if args.output_compression is not None:
        if output_format != "jpeg":
            fail("--output-compression is supported only for JPEG output")
        if not 0 <= args.output_compression <= 100:
            fail("output compression must be between 0 and 100")


def transparency_prompt(prompt: str, background: str) -> str:
    if background != "transparent":
        return prompt
    lowered = prompt.lower()
    parts: list[str] = []
    if "rgba image" not in lowered and "alpha channel" not in lowered:
        parts.append(TRANSPARENT_PREFIX)
    parts.append(prompt)
    if "background is transparent" not in lowered and "transparent background" not in lowered:
        parts.append(TRANSPARENT_SUFFIX)
    return " ".join(parts)


def normalize_base_url(raw: str) -> str:
    raw = raw.strip().rstrip("/")
    if not raw:
        fail("proxy base URL is empty")
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        fail("proxy base URL must be an absolute http:// or https:// URL")
    if parsed.username or parsed.password:
        fail("proxy base URL must not contain credentials")
    if parsed.query or parsed.fragment:
        fail("proxy base URL must not contain a query or fragment")
    path = parsed.path.rstrip("/")
    if not path.lower().endswith("/v1"):
        path = f"{path}/v1" if path else "/v1"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


@dataclass(frozen=True)
class Connection:
    base_url: str
    api_key: str
    source: str
    key_env: str


def codex_provider() -> tuple[str, str, str] | None:
    roots: list[Path] = []
    if os.getenv("CODEX_HOME"):
        roots.append(Path(os.environ["CODEX_HOME"]).expanduser())
    roots.append(Path.home() / ".codex")
    seen: set[Path] = set()
    for root in roots:
        path = root / "config.toml"
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        try:
            with path.open("rb") as handle:
                data = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError):
            continue
        provider_id = data.get("model_provider")
        providers = data.get("model_providers")
        if not isinstance(provider_id, str) or not isinstance(providers, dict):
            continue
        provider = providers.get(provider_id)
        if not isinstance(provider, dict):
            continue
        base_url = provider.get("base_url")
        env_key = provider.get("env_key")
        if isinstance(base_url, str) and isinstance(env_key, str) and os.getenv(env_key):
            return base_url, env_key, f"codex provider {provider_id}"
    return None


def first_set(names: list[str]) -> tuple[str, str] | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return name, value
    return None


def discover_connection(args: argparse.Namespace, *, require_key: bool) -> Connection:
    explicit_base = args.base_url
    explicit_key_env = args.api_key_env

    base_url: str | None = None
    source = ""
    preferred_key_env: str | None = explicit_key_env

    if explicit_base:
        base_url, source = explicit_base, "--base-url"
    elif os.getenv("QWEN_IMAGE_BASE_URL"):
        base_url, source = os.environ["QWEN_IMAGE_BASE_URL"], "QWEN_IMAGE_BASE_URL"
        preferred_key_env = preferred_key_env or "QWEN_IMAGE_API_KEY"
    else:
        codex = codex_provider()
        if codex:
            base_url, preferred_key_env, source = codex
        else:
            match = first_set(list(DEFAULTS.get("base_url_envs", [])))
            if match:
                base_name, base_url = match
                source = base_name
                paired = {
                    "OPENAI_BASE_URL": "OPENAI_API_KEY",
                    "ANTHROPIC_BASE_URL": "ANTHROPIC_AUTH_TOKEN",
                }
                preferred_key_env = preferred_key_env or paired.get(base_name)

    if not base_url:
        fail(
            "cannot discover the current Images API address; set QWEN_IMAGE_BASE_URL "
            "or pass --base-url"
        )

    if explicit_key_env:
        key_candidates = [explicit_key_env]
    elif preferred_key_env:
        key_candidates = [preferred_key_env]
        if preferred_key_env == "ANTHROPIC_AUTH_TOKEN":
            key_candidates.append("ANTHROPIC_API_KEY")
    elif source == "--base-url":
        # Avoid accidentally sending an unrelated public-provider credential to
        # an explicitly supplied host. Local overrides are the only safe
        # implicit candidates; otherwise require --api-key-env.
        key_candidates = ["QWEN_IMAGE_API_KEY"]
    else:
        key_candidates = list(DEFAULTS.get("api_key_envs", []))

    key_match = first_set(key_candidates)
    key_env, api_key = key_match if key_match else (preferred_key_env, "")
    if require_key and (not key_env or not api_key):
        requested = explicit_key_env or preferred_key_env or "QWEN_IMAGE_API_KEY"
        fail(f"API key environment variable is not set: {requested}")

    return Connection(
        base_url=normalize_base_url(base_url),
        api_key=api_key,
        source=source,
        key_env=key_env or "",
    )


def read_limited(response: Any, limit: int) -> bytes:
    declared = response.headers.get("Content-Length")
    if declared:
        try:
            if int(declared) > limit:
                fail(f"response exceeds the {limit}-byte safety limit")
        except ValueError:
            pass
    data = response.read(limit + 1)
    if len(data) > limit:
        fail(f"response exceeds the {limit}-byte safety limit")
    return data


def request_bytes(
    url: str,
    *,
    method: str,
    api_key: str,
    timeout: float,
    body: bytes | None = None,
    content_type: str | None = None,
    limit: int,
) -> bytes:
    headers = {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}
    if content_type:
        headers["Content-Type"] = content_type
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            return read_limited(response, limit)
    except HTTPError as exc:
        detail = exc.read(8192).decode("utf-8", errors="replace").strip()
        fail(f"HTTP {exc.code} from {url}: {detail or exc.reason}")
    except URLError as exc:
        fail(f"cannot reach {url}: {exc.reason}")
    except TimeoutError:
        fail(f"request timed out: {url}")
    return b""


def parse_json(data: bytes, source: str) -> dict[str, Any]:
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        fail(f"{source} returned invalid JSON: {exc}")
    if not isinstance(value, dict):
        fail(f"{source} returned a non-object JSON response")
    return value


def check_available(connection: Connection, model: str) -> list[str]:
    url = f"{connection.base_url}/models"
    raw = request_bytes(
        url,
        method="GET",
        api_key=connection.api_key,
        timeout=float(DEFAULTS.get("preflight_timeout_seconds", 15)),
        limit=2 * 1024 * 1024,
    )
    payload = parse_json(raw, "/v1/models")
    items = payload.get("data")
    if not isinstance(items, list):
        fail("/v1/models response has no data list")
    models = [item.get("id") for item in items if isinstance(item, dict) and isinstance(item.get("id"), str)]
    if model not in models:
        shown = ", ".join(models[:20]) or "none"
        fail(f"image model alias {model!r} is unavailable; advertised models: {shown}")
    return models


def ensure_reference_images(values: list[str]) -> list[Path]:
    maximum = int(DEFAULTS.get("max_reference_images", 5))
    if not values:
        fail("edit requires at least one --image")
    if len(values) > maximum:
        fail(f"at most {maximum} reference images are allowed per edit request")
    byte_limit = int(DEFAULTS.get("max_reference_bytes", 50 * 1024 * 1024))
    paths: list[Path] = []
    for value in values:
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            fail(f"reference image not found: {path}")
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
            fail(f"reference image must be PNG or JPEG: {path}")
        if path.stat().st_size > byte_limit:
            fail(f"reference image exceeds the {byte_limit}-byte limit: {path}")
        paths.append(path)
    return paths


def json_body(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def multipart_body(fields: dict[str, Any], images: list[Path]) -> tuple[bytes, str]:
    boundary = f"----qwen-image-local-{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for name, value in fields.items():
        if value is None:
            continue
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        else:
            rendered = str(value)
        parts.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                rendered.encode("utf-8"),
                b"\r\n",
            ]
        )
    for path in images:
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        safe_name = path.name.replace('"', "_")
        parts.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="image[]"; filename="{safe_name}"\r\n'.encode(),
                f"Content-Type: {mime}\r\n\r\n".encode(),
                path.read_bytes(),
                b"\r\n",
            ]
        )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def output_path(raw: str | None, output_format: str) -> Path:
    extension = ".jpg" if output_format == "jpeg" else ".png"
    if raw:
        path = Path(raw).expanduser()
        if not path.suffix:
            path = path.with_suffix(extension)
        valid_suffixes = {".jpg", ".jpeg"} if output_format == "jpeg" else {".png"}
        if path.suffix.lower() not in valid_suffixes:
            fail(f"output path extension does not match {output_format}: {path}")
    else:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = Path("output/qwen-image") / f"qwen-image-{stamp}{extension}"
    return path.resolve()


def decode_image(payload: dict[str, Any], output_format: str) -> bytes:
    data = payload.get("data")
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        fail("image response must contain exactly one data item")
    encoded = data[0].get("b64_json")
    if not isinstance(encoded, str) or not encoded:
        fail("image response does not contain data[0].b64_json")
    try:
        image = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        fail(f"image response contains invalid base64: {exc}")
    if output_format == "png" and not image.startswith(b"\x89PNG\r\n\x1a\n"):
        fail("server returned data that is not a PNG")
    if output_format == "jpeg" and not image.startswith(b"\xff\xd8\xff"):
        fail("server returned data that is not a JPEG")
    return image


def write_output(path: Path, data: bytes, *, force: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "wb" if force else "xb"
    try:
        with path.open(mode) as handle:
            handle.write(data)
    except FileExistsError:
        fail(f"output already exists: {path}; choose another name or use --force")


def base_payload(args: argparse.Namespace, prompt: str, output_format: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": args.model,
        "prompt": prompt,
        "n": int(DEFAULTS["n"]),
        "size": args.size,
        "num_inference_steps": int(DEFAULTS["num_inference_steps"]),
        "guidance_scale": args.guidance_scale,
        "seed": args.seed,
        "generator_device": DEFAULTS["generator_device"],
        "output_format": output_format,
        "response_format": DEFAULTS["response_format"],
        "background": args.background,
    }
    if args.negative_prompt:
        payload["negative_prompt"] = args.negative_prompt
    if args.output_compression is not None:
        payload["output_compression"] = args.output_compression
    return payload


def run_check(args: argparse.Namespace) -> int:
    connection = discover_connection(args, require_key=not args.dry_run)
    if args.dry_run:
        print(json.dumps({"dry_run": True, "model": args.model, "models_url": f"{connection.base_url}/models", "connection_source": connection.source}, indent=2))
        return 0
    models = check_available(connection, args.model)
    print(json.dumps({"available": True, "model": args.model, "base_url": connection.base_url, "connection_source": connection.source, "advertised_model_count": len(models)}, ensure_ascii=False, indent=2))
    return 0


def run_image(args: argparse.Namespace) -> int:
    output_format = normalize_output_format(args.output_format)
    _, _, ratio = validate_size(args.size)
    validate_sampling(args, output_format)
    prompt = transparency_prompt(read_prompt(args.prompt, args.prompt_file), args.background)
    references = ensure_reference_images(args.image) if args.command == "edit" else []
    path = output_path(args.out, output_format)

    connection = discover_connection(args, require_key=not args.dry_run)
    payload = base_payload(args, prompt, output_format)
    endpoint = f"{connection.base_url}/images/{'edits' if args.command == 'edit' else 'generations'}"
    if args.command == "generate":
        payload["enable_cache_dit"] = args.enable_cache_dit

    if args.dry_run:
        public_payload = dict(payload)
        if references:
            public_payload["image[]"] = [str(path) for path in references]
        print(json.dumps({"dry_run": True, "endpoint": endpoint, "connection_source": connection.source, "aspect_ratio": ratio, "output_path": str(path), "payload": public_payload}, ensure_ascii=False, indent=2))
        return 0

    check_available(connection, args.model)
    if args.command == "generate":
        body = json_body(payload)
        content_type = "application/json"
    else:
        body, content_type = multipart_body(payload, references)

    raw = request_bytes(
        endpoint,
        method="POST",
        api_key=connection.api_key,
        timeout=float(args.timeout),
        body=body,
        content_type=content_type,
        limit=int(DEFAULTS.get("max_response_bytes", 128 * 1024 * 1024)),
    )
    response = parse_json(raw, endpoint)
    image = decode_image(response, output_format)
    write_output(path, image, force=args.force)
    print(json.dumps({"ok": True, "operation": args.command, "path": str(path), "model": args.model, "size": args.size, "aspect_ratio": ratio, "seed": args.seed, "output_format": output_format, "reference_images": len(references), "bytes": len(image)}, ensure_ascii=False, indent=2))
    return 0


def add_shared_connection(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--base-url", help="OpenAI-compatible API base URL; auto-discovered when omitted")
    parser.add_argument("--api-key-env", help="name of the environment variable containing the API key")
    parser.add_argument("--model", default=os.getenv("QWEN_IMAGE_MODEL", DEFAULTS["model"]), help="proxy model alias")
    parser.add_argument("--dry-run", action="store_true", help="validate and print the sanitized request without network access")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_shared_connection(parser)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("check", help="check whether the configured model alias is advertised")

    for name in ("generate", "edit"):
        child = subparsers.add_parser(name, help="generate a new image" if name == "generate" else "edit using one to five references")
        child.add_argument("--prompt")
        child.add_argument("--prompt-file")
        child.add_argument("--size", default=DEFAULTS["default_size"])
        child.add_argument("--seed", type=int, default=int(DEFAULTS["seed"]))
        child.add_argument("--guidance-scale", type=float, default=float(DEFAULTS["guidance_scale"]))
        child.add_argument("--negative-prompt")
        child.add_argument("--output-format", default=DEFAULTS["output_format"], choices=["png", "jpeg", "jpg"])
        child.add_argument("--output-compression", type=int)
        child.add_argument("--background", default=DEFAULTS["background"], choices=["auto", "transparent"])
        child.add_argument("--out")
        child.add_argument("--force", action="store_true")
        child.add_argument("--timeout", type=float, default=float(DEFAULTS["request_timeout_seconds"]))
        if name == "generate":
            child.add_argument("--enable-cache-dit", action=argparse.BooleanOptionalAction, default=bool(DEFAULTS["enable_cache_dit"]))
            child.set_defaults(image=[])
        else:
            child.add_argument("--image", action="append", required=True, help="ordered PNG/JPEG reference; repeat up to five times")
            child.set_defaults(enable_cache_dit=False)
    return parser


def main() -> int:
    try:
        args = build_parser().parse_args()
        if args.command == "check":
            return run_check(args)
        return run_image(args)
    except CliError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Error: canceled", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
