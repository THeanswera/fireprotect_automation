# AGENTS.md

## Scope

These instructions apply to the `fireprotect-automation` repository rooted at this directory.

## Safety and execution boundaries

- Treat the project as a safety-oriented engineering/reverse-engineering framework, not as a production-approved calculation tool.
- Do not start LIRA, RX3, Microsoft Excel, or any GUI calculation workflow unless the user explicitly requests it.
- Do not infer engineering values, RX38 field meanings, LIRA axis/sign conventions, governing-result selection, or material/normative validity from patterns alone.
- Keep `DRAFT`, `VALIDATION`, and `PRODUCTION` semantics fail-closed. Warnings do not replace evidence.
- Preserve the distinction between `ProjectElement`, `Rx3Input`, and `Rx3Result`.
- Treat `CONFIRMED`, `PROBABLE`, and `UNKNOWN` as evidence states, not as interchangeable implementation hints. `PROBABLE` and `UNKNOWN` RX38 fields are not writable through the typed API.
- Preserve raw source tokens, units, source locations, hashes, and provenance when reviewing or extending adapters.

## Change boundaries

- A read-only review must not modify source code, tests, configuration, validation evidence, RX38/RXDB inputs, Excel/PDF/source artifacts, or generated results.
- Documentation-only files may be created or updated when the user explicitly asks for a status note, operating instructions, or equivalent documentation.
- Before changing an existing file, read it first. Prefer targeted edits over broad rewrites.
- Do not overwrite templates, source files, generated outputs, or existing evidence bundles.

## Evidence and reporting

- Separate what is implemented in code from what is demonstrated by controlled evidence and what remains unknown.
- Do not present historical pytest, ruff, mypy, RX3, Excel, or validation results as results of the current session unless they were actually run in the current session.
- A successful parser/test or a file opening in RX3 proves software behavior/readability only; it does not prove normative correctness or engineering equivalence.
- The repository's current release status must remain `NOT_READY_FOR_ISSUE` unless the required external evidence and release gates are explicitly closed.
- For LIRA → RX3, do not use an envelope rule such as `max(abs(all_values))` unless governing-result semantics are independently validated.

## Review checklist

When reviewing the project, inspect the applicable README, `pyproject.toml`, Git status, `docs/PROGRESS.md`, `docs/OPEN_QUESTIONS.md`, `docs/SAFETY_MODEL.md`, `docs/PIPELINE_MVP.md`, and `docs/RX3_CONTROLLED_EXPERIMENTS.md`. Also inspect the relevant implementation under `src/fireprotect/model.py`, `src/fireprotect/lira/`, and `src/fireprotect/rx3/`.

Record:

1. current Git ref and working-tree state;
2. implemented transformations and their explicit units;
3. provenance and source-fingerprint behavior;
4. confirmed mappings and their exact evidence scope;
5. unresolved mappings, blockers, and external checks still required;
6. whether any commands were actually executed in the current session.
