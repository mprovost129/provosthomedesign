from datetime import date, datetime, time, timedelta
from decimal import Decimal
from importlib import import_module
from unittest.mock import patch
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase, SimpleTestCase, override_settings
from django.urls import reverse

from .access import mint_email_token
from .forecasting import calculate_forecasts, get_schedule, queue_forecasts
from .forms import QueueEditForm
from .models import DesignSchedule, NotificationDelivery, QueueState, Status, WorkItem
from .schedule_views import DesignScheduleForm, save_schedule
from .services import EditConflict, create_work, edit_work
from .tests import work_data

EASTERN = ZoneInfo("America/New_York")
MONDAY = datetime(2026, 11, 9, 8, 30, tzinfo=EASTERN)


class ForecastAlgorithmTests(SimpleTestCase):
    def item(self, **values):
        defaults = dict(status=Status.READY, estimated_hours=Decimal("8"))
        defaults.update(values)
        return WorkItem(**defaults)

    def forecast(self, items, now=MONDAY, **schedule):
        return calculate_forecasts(items, DesignSchedule(**schedule), now)

    def test_default_eight_hours_and_five_hour_days_accumulate(self):
        a, b, c = [self.item() for _ in range(3)]
        f = self.forecast([a, b, c])
        self.assertEqual((f[a.pk].start, f[a.pk].end), (date(2026,11,10), date(2026,11,12)))
        self.assertEqual((f[b.pk].start, f[b.pk].end), (date(2026,11,12), date(2026,11,16)))
        self.assertEqual(f[c.pk].start, date(2026,11,13))

    def test_waiting_closed_and_deleted_do_not_consume_capacity(self):
        excluded = [self.item(status=s) for s in (Status.NEEDS_INFORMATION, Status.ON_HOLD, Status.COMPLETED, Status.CANCELED)]
        excluded.append(self.item(deleted_at=MONDAY))
        ready = self.item()
        f = self.forecast(excluded + [ready])
        self.assertEqual(f[ready.pk].start, date(2026,11,10))
        self.assertTrue(all(f[i.pk].start is None and f[i.pk].message for i in excluded))

    def test_after_hours_weekends_and_holidays_are_skipped(self):
        a = self.item(estimated_hours=Decimal("5"))
        for now in (datetime(2026,11,6,15,30,tzinfo=EASTERN), datetime(2026,11,7,9,tzinfo=EASTERN)):
            f = self.forecast([a], now, days_off=["2026-11-09", "2026-11-11"])[a.pk]
            self.assertEqual((f.start,f.end), (date(2026,11,10),date(2026,11,13)))

    def test_intraday_capacity_and_exact_boundary(self):
        a = self.item(estimated_hours=Decimal("2.5"))
        midday = MONDAY.replace(hour=12, minute=0)
        self.assertEqual(self.forecast([a],midday)[a.pk].start, MONDAY.date())
        a.estimated_hours=Decimal("2.51")
        self.assertEqual(self.forecast([a],midday)[a.pk].start, date(2026,11,10))
        a.estimated_hours=Decimal("5")
        self.assertEqual(self.forecast([a],MONDAY.replace(hour=7))[a.pk].start, MONDAY.date())

    def test_scheduled_start_reserves_future_capacity_without_wasting_earlier_days(self):
        a = self.item(scheduled_start=date(2026,11,12), estimated_hours=Decimal("10"))
        b = self.item(estimated_hours=Decimal("20"))
        f = self.forecast([a,b])
        self.assertEqual(f[a.pk].start,date(2026,11,13))
        self.assertEqual(f[b.pk].start,date(2026,11,16))

    def test_custom_capacity_window_and_zero_hours(self):
        a=self.item(estimated_hours=Decimal("8"));b=self.item(estimated_hours=Decimal("0"))
        f=self.forecast([a,b], design_hours_per_day=Decimal("4"), window_business_days=0)
        self.assertEqual(f[a.pk].start,date(2026,11,10));self.assertEqual(f[a.pk].end,f[a.pk].start)
        self.assertEqual(f[b.pk].start,date(2026,11,11))

    def test_eastern_timezone_is_used_across_daylight_saving(self):
        a=self.item(estimated_hours=Decimal("5"))
        f=self.forecast([a],datetime(2026,11,9,13,30,tzinfo=ZoneInfo("UTC")))[a.pk]
        self.assertEqual(f.start,date(2026,11,9))
        f=self.forecast([a],datetime(2026,3,9,12,30,tzinfo=ZoneInfo("UTC")))[a.pk]
        self.assertEqual(f.start,date(2026,3,9))

    def test_huge_estimate_fails_safely_without_unbounded_work(self):
        a=self.item(estimated_hours=Decimal("999999.99"))
        self.assertIsNone(self.forecast([a])[a.pk].start)


@override_settings(WORK_QUEUE_ENABLED=True, WORK_INTAKE_ENABLED=True)
class ForecastIntegrationTests(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_superuser("forecast-owner","owner@example.invalid","test-only")
        self.client.force_login(self.owner)
        self.item=create_work(data=work_data(), actor=self.owner)[0]

    def payload(self, **changes):
        form=QueueEditForm(instance=self.item)
        data={name: getattr(self.item,name) for name in form.Meta.fields}
        data.update(project=str(self.item.project_id),version=self.item.version,client_email="skip")
        data={k: ("" if v is None else v) for k,v in data.items()}
        data.update(changes)
        return data

    def test_defaults_and_legacy_conversion_preserve_existing_data(self):
        self.assertEqual(self.item.estimated_hours,8)
        s=get_schedule();self.assertEqual(s.design_hours_per_day,5)
        self.assertEqual((s.workday_start,s.workday_end),(time(8,30),time(15,30)))
        WorkItem.objects.filter(pk=self.item.pk).update(estimated_days=Decimal("2.5"))
        migration=import_module("workqueue.migrations.0064_design_delivery_forecasts")
        migration.seed_schedule_and_estimates(apps, SimpleNamespace(connection=connection))
        self.item.refresh_from_db()
        self.assertEqual(self.item.estimated_hours,Decimal("12.5"))
        self.assertEqual(self.item.estimated_days,Decimal("2.5"))
        self.assertEqual(NotificationDelivery.objects.count(),0)

    def test_hours_form_blank_reset_omitted_preserve_and_validation(self):
        self.item.estimated_hours=Decimal("12.5");self.item.save()
        for value,expected in [("",Decimal("8")),("3.25",Decimal("3.25")),("0",Decimal("0"))]:
            form=QueueEditForm(self.payload(estimated_hours=value),instance=self.item)
            self.assertTrue(form.is_valid(),form.errors);self.assertEqual(form.cleaned_data['estimated_hours'],expected)
            self.item.refresh_from_db()
        data=self.payload();del data['estimated_hours']
        form=QueueEditForm(data,instance=self.item)
        self.assertTrue(form.is_valid(),form.errors);self.assertEqual(form.cleaned_data['estimated_hours'],Decimal("12.5"))
        for value in ("-1","nonsense","99999999999","1.001"):
            self.item.refresh_from_db();form=QueueEditForm(self.payload(estimated_hours=value),instance=self.item)
            self.assertFalse(form.is_valid());self.assertIn('estimated_hours',form.errors)

    def test_edits_are_versioned_audited_and_do_not_send_emails(self):
        edit_work(item_id=self.item.pk,version=1,data={"estimated_hours":Decimal("3"),"client_action":"Please send your survey."},actor=self.owner)
        self.item.refresh_from_db();self.assertEqual(self.item.version,2)
        self.assertTrue(self.item.audit_events.filter(changes__has_key="estimated_hours").exists())
        with self.assertRaises(EditConflict):
            edit_work(item_id=self.item.pk,version=1,data={"estimated_hours":Decimal("2")},actor=self.owner)
        self.assertEqual(NotificationDelivery.objects.count(),0)

    def test_global_queue_forecast_filters_and_reordering(self):
        second=create_work(data=work_data(contact_full_name="Second Person",priority="urgent"),actor=self.owner)[0]
        with patch("workqueue.forecasting.timezone.now",return_value=MONDAY):
            response=self.client.get(reverse("workqueue:queue"),{"q":"Second Person"})
            self.assertEqual(len(response.context['rows']),1)
            self.assertContains(response,'Estimated delivery')
            self.assertContains(response,'Nov 12, 2026')
            self.assertContains(response,'Nov 16, 2026')
            self.assertEqual(response.context['rows'][0]['item'].delivery_forecast.start,date(2026,11,12))
            WorkItem.objects.filter(pk=second.pk).update(queue_order=1,received_at=MONDAY-timedelta(days=2))
            self.assertEqual(queue_forecasts()[second.pk].start,date(2026,11,10))

    def test_tracking_exposes_only_authorized_request_and_escaped_client_copy(self):
        WorkItem.objects.filter(pk=self.item.pk).update(client_action='<script>needed</script>',internal_notes='PRIVATE-NOTE',next_deliverable='Revision drawings')
        other=create_work(data=work_data(contact_email='other@example.invalid',project_name='PRIVATE-OTHER'),actor=self.owner)[0]
        self.client.logout()
        response=self.client.get(reverse('workqueue:tracking'))
        self.assertNotContains(response,'Revision drawings')
        _,token=mint_email_token(self.item.contact_email,self.item.reference)
        self.client.post(reverse('workqueue:confirm_access'),{'token':token})
        with patch('workqueue.forecasting.timezone.now',return_value=MONDAY):
            response=self.client.get(reverse('workqueue:tracking'))
        self.assertContains(response,'Revision drawings');self.assertContains(response,'&lt;script&gt;needed&lt;/script&gt;')
        self.assertNotContains(response,'PRIVATE-NOTE');self.assertNotContains(response,'PRIVATE-OTHER');self.assertNotContains(response,other.reference)
        self.assertNotContains(response,'<script>needed</script>')

    def schedule_data(self,**changes):
        data=dict(version=1,design_hours_per_day='5',workday_start='08:30',workday_end='15:30',window_business_days='2',days_off_text='2026-11-26')
        data.update(changes);return data

    def test_schedule_changes_and_stale_submission(self):
        url=reverse('workqueue:design_schedule')
        response=self.client.post(url,self.schedule_data())
        self.assertEqual(response.status_code,302)
        self.assertEqual(get_schedule().days_off,['2026-11-26'])
        self.assertEqual(get_schedule().version,2)
        response=self.client.post(url,self.schedule_data(design_hours_per_day='4'))
        self.assertEqual(response.status_code,409);self.assertEqual(get_schedule().design_hours_per_day,5)
        self.assertEqual(NotificationDelivery.objects.count(),0)

    def test_invalid_schedule_and_permissions(self):
        url=reverse('workqueue:design_schedule')
        for changes in [dict(design_hours_per_day='0'),dict(design_hours_per_day='8'),dict(workday_end='08:00'),dict(window_business_days='11'),dict(days_off_text='tomorrow')]:
            response=self.client.post(url,self.schedule_data(**changes))
            self.assertEqual(response.status_code,400,changes)
        staff=get_user_model().objects.create_user('read-only',is_staff=True)
        staff.user_permissions.add(Permission.objects.get(codename='view_workitem'))
        self.client.force_login(staff)
        self.assertEqual(self.client.get(url).status_code,403)
        self.assertEqual(self.client.post(url,self.schedule_data()).status_code,403)
        self.client.logout();self.assertEqual(self.client.get(url).status_code,302)
