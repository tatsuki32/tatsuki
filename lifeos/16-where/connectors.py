"""Where Drive and Gmail come in. Both readers are swappable.

/where runs inside Claude Code, where Claude reads Drive and Gmail with its connectors and
passes the result to `where.py render`. These functions are for running without Claude:
replace one (or pass readers= to core.gather) and `where.py show` uses it too.

A reader takes the project name and returns [{"title": ..., "date": "YYYY-MM-DD"}, ...],
or None when it is not connected. Readers only read.
"""


def read_drive(name):
    return None


def read_gmail(name):
    return None


def drive_query(name):
    """Query for the Drive connector's search_files."""
    words = str(name or "").split()
    safe = [w.replace("\\", "\\\\").replace("'", "\\'") for w in words]
    return " and ".join("fullText contains '%s'" % w for w in safe)


def gmail_query(name):
    """Query for the Gmail connector's search_threads. Unquoted keywords, last year only."""
    words = str(name or "").replace('"', " ").split()
    return " ".join(words + ["newer_than:1y"])
