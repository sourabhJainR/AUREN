# Execute

Implement the approved change using the repository's existing language, architecture, segregation, and engineering conventions.

Rules:
- Inspect relevant files before editing.
- Run the repository convention profiler when infrastructure or cross-cutting code is involved.
- Before creating any new interface, class, constant, configuration, test, adapter, or utility file, inspect existing sibling locations and identify the repository's segregation pattern.
- Prefer an existing cohesive directory/module/package over creating a new folder.
- When multiple candidate locations exist, compare them by domain cohesion, dependency direction, naming/namespace consistency, existing test proximity, and reuse by neighboring code; choose the strongest fit.
- Keep interfaces/contracts close to the owning abstraction unless the repository clearly separates contracts.
- Keep constants close to the bounded context that owns them unless they are truly shared across multiple contexts.
- Keep tests in the repository's established test location and mirror the production structure when that is the existing convention.
- Do not create a generic Shared/Common/Utils location merely because it is convenient.
- Record the placement decision and why it was selected in the run evidence.
- Keep the change focused and compatible.
- Apply only the principles materially relevant to the task.
- Reuse existing dependencies and patterns.
- Do not modify unrelated files.
- Add or update behavior-focused tests where appropriate.
- Do not claim validation until command output exists.

Before handing off:
- summarize changed behavior
- identify files touched
- identify placement decisions and the repository pattern they follow
- identify assumptions
- identify anything that still needs validation


Communication and data format:
- Use short, direct technical sentences.
- Separate verified facts, assumptions, and unknowns.
- Name the revision and evidence for status claims.
- Prefer TOON for structured model-facing payloads when the receiver supports it.
- Keep JSON where the API, configuration, or CLI contract requires JSON.
- Do not change a format without validation, regression tests, and a rollback path.
