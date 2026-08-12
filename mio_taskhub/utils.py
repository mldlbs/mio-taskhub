from datetime import datetime, timezone


def _now():
    return datetime.now(timezone.utc)
