import json
from datetime import datetime
from typing import Optional

import pytz


def is_within_active_hours(active_hours_json: Optional[str]) -> bool:
    """Check if current time is within active hours."""
    if not active_hours_json:
        return True

    try:
        config = json.loads(active_hours_json)
        tz = pytz.timezone(config.get("timezone", "UTC"))
        now = datetime.now(tz)
        current_time = now.strftime("%H:%M")

        for range_ in config.get("ranges", []):
            start = range_.get("start", "00:00")
            end = range_.get("end", "23:59")

            if start <= end:
                if start <= current_time <= end:
                    return True
            else:
                if current_time >= start or current_time <= end:
                    return True

        return False
    except (json.JSONDecodeError, KeyError):
        return True


def parse_time_range(text: str) -> Optional[dict]:
    """Parse time range from user input.

    Expected format: "10:00-13:00" or "10:00 - 13:00"
    """
    text = text.replace(" ", "")

    if "-" not in text:
        return None

    parts = text.split("-")
    if len(parts) != 2:
        return None

    start, end = parts

    try:
        datetime.strptime(start, "%H:%M")
        datetime.strptime(end, "%H:%M")
    except ValueError:
        return None

    return {"start": start, "end": end}


def format_active_hours(active_hours_json: Optional[str]) -> str:
    """Format active hours for display."""
    if not active_hours_json:
        return "24/7 (без ограничений)"

    try:
        config = json.loads(active_hours_json)
        timezone = config.get("timezone", "UTC")
        ranges = config.get("ranges", [])

        if not ranges:
            return "24/7 (без ограничений)"

        ranges_str = ", ".join([f"{r['start']}-{r['end']}" for r in ranges])
        return f"{ranges_str} ({timezone})"
    except (json.JSONDecodeError, KeyError):
        return "Ошибка формата"


def create_active_hours_json(ranges: list[dict], timezone: str = "Europe/Kiev") -> str:
    """Create active hours JSON string."""
    return json.dumps({
        "timezone": timezone,
        "ranges": ranges
    })
