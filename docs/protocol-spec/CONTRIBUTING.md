# Contributing

Thanks for contributing to the AriaCast protocol specification.

## Scope

This repository accepts:
- protocol clarifications
- compatibility fixes
- schema updates aligned with spec text
- backward-compatible extensions

## Rules for protocol changes

1. Update all impacted files in `spec/`.
2. Keep message structures consistent across prose and schemas.
3. For breaking changes, add a migration note in `docs/roadmap.md`.
4. Include concrete examples for new fields or message types.

## Pull request checklist

- [ ] Changes are scoped and protocol-focused
- [ ] Message examples are valid JSON
- [ ] Related schemas in `schemas/` are updated
- [ ] No contradictions across `spec/*.md`
