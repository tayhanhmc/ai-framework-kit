# CSV export for the finance importer

Target: `repo/`, branch `main`

## Objective

`export_rows` in `app/export.py` renders user rows for the finance team's importer.

## Acceptance criteria

- Header row of the display labels in `COLUMNS` order.
- Quoting per RFC 4180 (use the `csv` module).
- Every line, the header included, ends with `\r\n`.
- `None` renders as an empty field.
- `joined` is a `datetime`; export only its date as `YYYY-MM-DD`.

## Decisions

- `\r\n` and date-only `joined`: the finance importer rejects anything else (agreed
  with finance on 2026-10-09).

## Progress

- [x] Header labels (evidence: `tests/test_export.py::test_header` passes)
- [ ] Data rows

## Next action

Replace the manual header write in `export_rows` with
`csv.writer(out, lineterminator="\r\n")`, write the header through it, then one row
per record applying the `None` and `joined` rules; add tests for each criterion.
