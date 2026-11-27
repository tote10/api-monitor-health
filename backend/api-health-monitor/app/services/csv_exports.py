import csv
import io

from sqlalchemy import select
from sqlalchemy.orm import Session


def spreadsheet_safe(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def stream_rows(engine, columns, table, scope, final_id):
    """Open one short-lived session per page and bound exports to their start."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([column.key for column in columns])
    yield buffer.getvalue()
    cursor = 0
    while cursor < final_id:
        with Session(engine) as db:
            page = db.execute(select(*columns).where(
                *scope, table.id > cursor, table.id <= final_id,
            ).order_by(table.id).limit(500)).all()
        if not page:
            return
        buffer.seek(0)
        buffer.truncate(0)
        writer.writerows([spreadsheet_safe(value) for value in row] for row in page)
        yield buffer.getvalue()
        cursor = page[-1][0]
