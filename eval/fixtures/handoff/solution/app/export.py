"""CSV export of user records."""

import csv
import datetime
import io

COLUMNS = [("id", "ID"), ("name", "Name"), ("joined", "Joined")]


def _cell(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, datetime.datetime):
        return value.date().isoformat()
    return value


def export_rows(rows: list[dict[str, object]]) -> str:
    """Render user rows as CSV text."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow([label for _, label in COLUMNS])
    for row in rows:
        writer.writerow([_cell(row.get(key)) for key, _ in COLUMNS])
    return out.getvalue()
