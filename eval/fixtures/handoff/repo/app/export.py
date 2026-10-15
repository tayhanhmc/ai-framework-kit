"""CSV export of user records."""

import io

COLUMNS = [("id", "ID"), ("name", "Name"), ("joined", "Joined")]


def export_rows(rows: list[dict[str, object]]) -> str:
    """Render user rows as CSV text."""
    out = io.StringIO()
    out.write(",".join(label for _, label in COLUMNS) + "\n")
    # TODO: write the data rows
    return out.getvalue()
