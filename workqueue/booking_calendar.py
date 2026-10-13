"""Presentation of verified month availability, with bounded month navigation."""
import calendar
from datetime import date,timedelta
from urllib.parse import urlencode
from django.conf import settings
from django.urls import reverse
from .calendar_provider import EASTERN


def month_start(day):
    return day.replace(day=1)


def shift_month(day,offset):
    index=day.year*12+day.month-1+offset
    return date(index//12,index%12+1,1)


def calendar_month(raw,selected,now):
    today=now.astimezone(EASTERN).date()
    first,last=month_start(today),month_start(today+timedelta(days=settings.BOOKING_HORIZON_DAYS))
    try:
        requested=date.fromisoformat(raw+'-01') if raw else month_start(selected or today)
    except (ValueError,TypeError):
        requested=first
    return min(max(requested,first),last)


def month_context(month,availability,selected,now,previous=None):
    today=now.astimezone(EASTERN).date()
    def url(**values):
        if previous:
            values['reschedule']=str(previous.pk)
        return reverse('workqueue:book')+'?'+urlencode(values)
    weeks=[]
    for week in calendar.Calendar(firstweekday=6).monthdatescalendar(month.year,month.month):
        weeks.append([{'date':day,'available':day.month == month.month and bool(availability.get(day)),
                       'selected':day == selected,'today':day == today,
                       'outside':day.month != month.month} for day in week])
    first=month_start(today)
    last=month_start(today+timedelta(days=settings.BOOKING_HORIZON_DAYS))
    prev_month,next_month=shift_month(month,-1),shift_month(month,1)
    earliest=min(availability) if availability else None
    return {'month':month,'weeks':weeks,'earliest':earliest,
            'earliest_url':url(day=earliest.isoformat())+'#meeting' if earliest else '',
            'previous_url':url(month=prev_month.strftime('%Y-%m')) if prev_month >= first else '',
            'next_url':url(month=next_month.strftime('%Y-%m')) if next_month <= last else '',
            'month_url':url(month=month.strftime('%Y-%m'))+'#booking-calendar'}
