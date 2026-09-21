# Prompting guidance

Read this file for generation prompt shaping, exact in-image text, multi-reference roles, and edit invariants.

## Specificity

- If the user's prompt is already detailed, preserve it and only organize it.
- If it is broad, add only details that materially improve the intended asset: medium, framing, lighting, usable negative space, or explicit constraints.
- Do not invent characters, brands, slogans, palette requirements, or narrative events.
- Include the intended use when it affects composition or polish.

Use this scaffold selectively:

```text
Use case: <photo | product mockup | UI mockup | infographic | illustration | concept art | logo exploration | educational visual>
Asset type: <where the image will be used>
Primary request: <the user's request>
Input images: <Picture 1: role; Picture 2: role> (editing only)
Scene/background: <environment>
Subject: <main subject>
Style/medium: <photo, illustration, 3D, etc.>
Composition/framing: <wide, close-up, top-down, placement>
Lighting/mood: <lighting and mood>
Color palette: <only if specified or clearly useful>
Text (verbatim): "<exact requested text>"
Constraints: <must preserve or include>
Avoid: <must not include>
```

Keep the final prompt concise; omit empty sections.

## Exact text

- Quote all required in-image text verbatim.
- State placement and typographic intent when important.
- Require the text exactly once and forbid extra text when appropriate.
- For a difficult word, spell it letter-by-letter in addition to providing the exact phrase.

## Generation

Structure generation prompts as scene/background, subject, important details, composition, and constraints. Use camera/framing language for photographic work. Ask for negative space only when the consuming layout needs it.

## Editing and references

Label references by order and role:

```text
Picture 1: edit target and identity source.
Picture 2: style reference only.
Picture 3: object to insert.
```

Repeat edit invariants on every iteration:

```text
Change only the jacket color to navy. Preserve the face, body, pose, camera angle, background, lighting, and all other objects.
```

For compositing, state which subject comes from which picture and how perspective, scale, shadows, and lighting should be reconciled. Never send more than five references.

## Transparent output

For native transparency, use PNG and request alpha explicitly. The CLI will add the standard framing when needed, but the main prompt should still describe an isolated subject or intended transparent composition.

Recommended form:

```text
This is an RGBA image with transparency. <description of the isolated subject or composition>. The image has an alpha channel and the background is transparent. No floor, no backdrop, no border, and no unintended shadow outside the subject.
```

For transparent edits, explicitly say to preserve the existing alpha channel and transparent background.

## Iteration

- Inspect the output before claiming success.
- Make one targeted change per edit iteration.
- Re-state invariants even when only one detail changes.
- Use the previous output as the next edit reference; the server retains no prior editing state.
