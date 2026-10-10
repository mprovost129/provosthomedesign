from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class CRMBackfillMigrationTests(TransactionTestCase):
    def test_original_requests_and_snapshots_survive_case_insensitive_backfill(self):
        executor = MigrationExecutor(connection)
        before = [("workqueue", "0016_queueworkerhealth")]
        after = [("workqueue", "0018_populate_crm_clients")]
        latest = executor.loader.graph.leaf_nodes()
        executor.migrate(before)
        try:
            apps = executor.loader.project_state(before).apps
            State = apps.get_model("workqueue", "QueueState")
            State.objects.get_or_create(pk=1)
            Item = apps.get_model("workqueue", "WorkItem")
            Submission = apps.get_model("workqueue", "Submission")
            for n, email in enumerate(["Morgan@Example.com", "morgan@example.com", "", ""]):
                item = Item.objects.create(reference=f"MIGRATE-{n}", queue_order=n+1,
                    contact_full_name="Morgan Lee", contact_email=email, company="", contact_phone="",
                    description="Historical work", billing_zip="02769")
                Submission.objects.create(work_item=item, idempotency_key=f"migration-{n}", answers={"Original email": email})
            snapshot = list(Item.objects.order_by("reference").values("reference", "contact_email", "company", "contact_phone", "queue_order", "billing_zip"))
            executor = MigrationExecutor(connection)
            executor.migrate(after)
            apps = executor.loader.project_state(after).apps
            Item = apps.get_model("workqueue", "WorkItem")
            Contact = apps.get_model("workqueue", "ClientContact")
            self.assertEqual(Contact.objects.count(), 3)
            self.assertEqual(Item.objects.get(reference="MIGRATE-0").client_contact_id,
                             Item.objects.get(reference="MIGRATE-1").client_contact_id)
            self.assertEqual(list(Item.objects.order_by("reference").values("reference", "contact_email", "company", "contact_phone", "queue_order", "billing_zip")), snapshot)
            self.assertEqual(apps.get_model("workqueue", "Submission").objects.get(idempotency_key="migration-0").answers, {"Original email": "Morgan@Example.com"})
            self.assertEqual(Item.objects.filter(client_contact__isnull=True).count(), 0)
        finally:
            MigrationExecutor(connection).migrate(latest)
