from datetime import datetime, timezone


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


def ensure_utc(dt: datetime) -> datetime:
    """Coerce naive or localized datetimes to UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def epoch_millis(dt: datetime) -> int:
    """Return milliseconds since epoch for the provided datetime."""
    return int(ensure_utc(dt).timestamp() * 1000)
