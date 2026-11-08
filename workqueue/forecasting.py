"""Read-only capacity forecasts. Saved queue order is authoritative, including
priority moves. Hours are remaining effort, not a timer. Waiting work uses no
capacity. Future scheduled starts reserve future days without leaving earlier
available days idle for other ready requests. No data is changed by a forecast.
"""
from bisect import bisect_left
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
from django.utils import timezone
from .models import CLOSED_STATUSES, DesignSchedule, Status, WorkItem

EASTERN = ZoneInfo("America/New_York")
MAX_CALENDAR_DAYS = 3660

@dataclass(frozen=True)
class Forecast:
    start: date | None = None
    end: date | None = None
    message: str = ""
    hours: Decimal = Decimal("8")

def get_schedule():
    return DesignSchedule.objects.filter(pk=1).first() or DesignSchedule(pk=1)

def unavailable_forecast(item):
    if item.deleted_at or item.status in CLOSED_STATUSES:
        return Forecast(message="This request is closed; no further delivery is scheduled.", hours=item.estimated_hours)
    if item.status == Status.NEEDS_INFORMATION:
        return Forecast(message="Waiting for requested information. A new delivery estimate will appear when work is ready to resume.", hours=item.estimated_hours)
    if item.status == Status.ON_HOLD:
        return Forecast(message="Work is on hold. A new delivery estimate will appear when it resumes.", hours=item.estimated_hours)
    return Forecast(message="The delivery estimate needs a schedule review.", hours=item.estimated_hours)

def calculate_forecasts(items, schedule, now=None):
    now = (now or timezone.now()).astimezone(EASTERN)
    today = now.date()
    off = set(schedule.days_off)
    dates, capacity = [], []
    for offset in range(MAX_CALENDAR_DAYS):
        day = today + timedelta(days=offset)
        if day.weekday() >= 5 or day.isoformat() in off:
            continue
        available = Decimal(schedule.design_hours_per_day)
        if day == today:
            start = datetime.combine(day, schedule.workday_start, EASTERN)
            end = datetime.combine(day, schedule.workday_end, EASTERN)
            if now >= end:
                continue
            if now > start:
                available *= Decimal(str((end - now).total_seconds())) / Decimal(str((end - start).total_seconds()))
        dates.append(day)
        capacity.append(available)
    forecasts = {}
    first_free = 0
    for item in items:
        if item.deleted_at or item.status in (*CLOSED_STATUSES, Status.NEEDS_INFORMATION, Status.ON_HOLD):
            forecasts[item.pk] = unavailable_forecast(item)
            continue
        while first_free < len(capacity) and capacity[first_free] <= 0:
            first_free += 1
        index = max(first_free, bisect_left(dates, item.scheduled_start or today))
        remaining = Decimal(item.estimated_hours)
        finish = None
        while index < len(dates):
            available = capacity[index]
            if available > 0:
                used = min(available, remaining)
                capacity[index] -= used
                remaining -= used
                if remaining <= 0:
                    finish = index
                    break
            index += 1
        if finish is not None and finish + schedule.window_business_days < len(dates):
            forecasts[item.pk] = Forecast(dates[finish], dates[finish + schedule.window_business_days],
                "Estimated next delivery, subject to remaining work and schedule changes.", item.estimated_hours)
        else:
            forecasts[item.pk] = unavailable_forecast(item)
    return forecasts

def queue_forecasts(now=None):
    items = WorkItem.objects.active().only("id", "queue_order", "received_at", "status", "deleted_at", "estimated_hours", "scheduled_start")
    return calculate_forecasts(items, get_schedule(), now)

def attach_forecasts(items, forecasts=None):
    forecasts = queue_forecasts() if forecasts is None else forecasts
    for item in items:
        item.delivery_forecast = forecasts.get(item.pk) or unavailable_forecast(item)
    return items
