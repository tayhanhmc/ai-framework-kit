# Dependencies

- `packaging` is pinned to 26.3 in `repo/uv.lock`; run code with `uv run --frozen`.
  Since packaging 22.0 there is no `LegacyVersion`: `parse()` and `Version()` raise
  `packaging.version.InvalidVersion` for strings that are not PEP 440 versions.
- Do not change the pin or the lockfile without the user's approval; other services
  share it.
