from datetime import date,datetime,timedelta
from unittest.mock import patch
from django.test import TestCase,override_settings
from django.urls import reverse
from .booking import available_days,available_times
from .booking_calendar import calendar_month,month_context
from .calendar_provider import EASTERN,CalendarUnavailable
from .models import Appointment,BookingAccess,NotificationDelivery
from .test_booking import CalendarDouble


NOW=datetime(2026,10,12,9,tzinfo=EASTERN)


@override_settings(WORK_INTAKE_ENABLED=True,BOOKING_ENABLED=True,BOOKING_HORIZON_DAYS=60,
    DEBUG=True,INTAKE_LOCAL_DEVELOPMENT=True,RECAPTCHA_ENTERPRISE_API_KEY='',RECAPTCHA_SECRET_KEY='',RECAPTCHA_PRIVATE_KEY='')
class BookingCalendarTests(TestCase):
    def setUp(self):
        self.provider=CalendarDouble()
        clock=patch('django.utils.timezone.now',return_value=NOW);clock.start();self.addCleanup(clock.stop)
        adapter=patch('workqueue.booking.calendar_provider',return_value=self.provider);adapter.start();self.addCleanup(adapter.stop)
        BookingAccess.objects.create(email='alex@example.invalid')
        session=self.client.session
        session['queue_verified_email']='alex@example.invalid'
        session['queue_email_verified_until']=(NOW+timedelta(days=7)).timestamp();session.save()
        self.url=reverse('workqueue:book')

    def hold(self,day,email='other@example.invalid',hour=10,state='pending'):
        start=datetime.combine(day,datetime.min.time(),EASTERN)+timedelta(hours=hour)
        return Appointment.objects.create(email=email,full_name='PRIVATE CLIENT',phone='555',purpose='PRIVATE PURPOSE',
            starts_at=start,ends_at=start+timedelta(minutes=30),reserved_until=start+timedelta(hours=1),local_date=day,
            calendar_id='primary',idempotency_key=email+str(start),state=state)

    def month(self):
        return available_days(date(2026,10,1),date(2026,10,31),email='alex@example.invalid',provider=self.provider)

    def test_month_reads_provider_once_and_local_reservations_once(self):
        with patch.object(self.provider,'busy',wraps=self.provider.busy) as busy,self.assertNumQueries(1):
            result=self.month()
        self.assertEqual(busy.call_count,1)
        self.assertEqual(min(result),date(2026,10,13))
        self.assertNotIn(date(2026,10,12),result)
        self.assertNotIn(date(2026,10,17),result)

    def test_busy_date_is_disabled_and_nearby_open_day_is_visible(self):
        self.provider.external=[(datetime(2026,10,13,tzinfo=EASTERN),datetime(2026,10,14,tzinfo=EASTERN))]
        response=self.client.get(self.url)
        self.assertContains(response,'Tuesday, October 13, 2026 — unavailable')
        self.assertContains(response,'Wednesday, October 14, 2026 — available')
        self.assertEqual(response.context['booking_calendar']['earliest'],date(2026,10,14))
        self.assertEqual(response.context['slots'],[])
        self.assertFalse(Appointment.objects.exists());self.assertFalse(NotificationDelivery.objects.exists())

    def test_noon_block_respects_meeting_and_buffer_boundary(self):
        self.provider.external=[(datetime(2026,10,13,12,tzinfo=EASTERN),datetime(2026,10,14,tzinfo=EASTERN))]
        result=self.month()[date(2026,10,13)]
        self.assertEqual([value.strftime('%H:%M') for value in result],['10:00','10:30','11:00'])
        self.assertEqual(result,available_times(date(2026,10,13),email='alex@example.invalid',provider=self.provider))

    def test_full_days_removed_partial_days_keep_nonoverlapping_slots(self):
        day=date(2026,10,14)
        self.hold(day,hour=10);self.hold(day,email='third@example.invalid',hour=12)
        self.assertNotIn(day,self.month())
        self.hold(date(2026,10,15),hour=11)
        slots=self.month()[date(2026,10,15)]
        self.assertEqual([s.hour*60+s.minute for s in slots],[600,720,750,780])

    def test_notice_horizon_and_daylight_saving_are_preserved(self):
        with patch('django.utils.timezone.now',return_value=NOW.replace(hour=12,minute=45)):
            self.assertEqual([s.hour for s in self.month()[date(2026,10,13)]],[13])
        values=available_days(date(2026,11,1),date(2026,11,30),email='alex@example.invalid',provider=self.provider)
        self.assertEqual(values[date(2026,11,3)][0].utcoffset(),timedelta(hours=-5))
        values=available_days(date(2026,12,1),date(2026,12,31),email='alex@example.invalid',provider=self.provider)
        self.assertNotIn(date(2026,12,15),values)

    def test_reschedule_keeps_original_and_excludes_original_capacity_and_events(self):
        original=self.hold(date(2026,10,14),email='alex@example.invalid',state='confirmed')
        self.hold(date(2026,10,14),hour=12)
        with patch.object(self.provider,'busy',wraps=self.provider.busy) as busy:
            result=available_days(date(2026,10,1),date(2026,10,31),email='alex@example.invalid',previous=original,provider=self.provider)
        self.assertIn(date(2026,10,14),result)
        self.assertNotIn(original.starts_at,result[date(2026,10,14)])
        self.assertEqual(busy.call_args.kwargs['exclude'],original.event_ids)
        response=self.client.get(self.url,{'reschedule':original.pk})
        self.assertIn('reschedule=',response.context['booking_calendar']['next_url'])
        original.refresh_from_db();self.assertEqual(original.state,'confirmed')

    def test_month_navigation_clamped_and_year_boundary_supported(self):
        self.assertEqual(calendar_month('1900-01',None,NOW),date(2026,10,1))
        self.assertEqual(calendar_month('9999-12',None,NOW),date(2026,12,1))
        self.assertEqual(calendar_month('bad',None,NOW),date(2026,10,1))
        context=month_context(date(2026,12,1),{},None,datetime(2026,12,1,tzinfo=EASTERN))
        self.assertIn('month=2027-01',context['next_url'])

    def test_selected_day_shows_radio_times_back_link_and_retains_bad_input(self):
        response=self.client.get(self.url,{'day':'2026-10-14'})
        self.assertContains(response,'type="radio"')
        self.assertContains(response,'Back to calendar / choose another day')
        token=response.context['form'].initial['token']
        response=self.client.post(self.url,{'day':'2026-10-14','slot':'unavailable','token':token,
            'full_name':'Retain my name','phone':'555','purpose':'Retain my notes','terms_accepted':'on'})
        self.assertContains(response,'Retain my name',status_code=400)
        self.assertContains(response,'Retain my notes',status_code=400)
        self.assertFalse(Appointment.objects.exists())

    def test_anonymous_and_expired_verified_sessions_do_not_load_provider(self):
        with patch.object(self.provider,'busy') as busy:
            self.client.logout();self.client.get(self.url);busy.assert_not_called()
        session=self.client.session;session['queue_verified_email']='alex@example.invalid'
        session['queue_email_verified_until']=(NOW-timedelta(seconds=1)).timestamp();session.save()
        with patch.object(self.provider,'busy') as busy:
            response=self.client.get(self.url);busy.assert_not_called();self.assertNotContains(response,'calendar-grid')

    def test_provider_failure_fails_closed_without_event_details(self):
        with patch.object(self.provider,'busy',side_effect=CalendarUnavailable('PRIVATE PROVIDER ERROR')):
            response=self.client.get(self.url)
        self.assertContains(response,'We cannot check calendar availability right now.')
        self.assertNotContains(response,'PRIVATE PROVIDER ERROR')
        self.assertEqual(response.context['calendar_slots'],{})
        self.assertNotContains(response,'id="appointment-form"')
        self.assertIn('no-store',response['Cache-Control'])
