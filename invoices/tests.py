import csv
import tempfile
from io import BytesIO, StringIO
from unittest.mock import patch
from pathlib import Path
from PIL import Image
from pypdf import PdfWriter
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from accounts.models import Business, Membership
from .models import Invoice
from .services import create_upload, validate_document


def image_upload(name="sample.png", colour="white", fmt="PNG"):
    out = BytesIO()
    Image.new("RGB", (32, 32), colour).save(out, format=fmt)
    return SimpleUploadedFile(name, out.getvalue())


def pdf_upload(pages=1, encrypted=False):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=100, height=100)
    if encrypted:
        writer.encrypt("test-password")
    output = BytesIO()
    writer.write(output)
    return SimpleUploadedFile("sample.pdf", output.getvalue())


class InvoiceTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.temp.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        User = get_user_model()
        self.user = User.objects.create_user("client", password="Testing-local-947!")
        self.other_user = User.objects.create_user("other", password="Testing-local-947!")
        self.reviewer = User.objects.create_user("accountant", password="Testing-local-947!")
        self.business = Business.objects.create(name="Fictional One")
        self.other = Business.objects.create(name="Fictional Two")
        Membership.objects.create(user=self.user, business=self.business, role="client")
        Membership.objects.create(user=self.other_user, business=self.other, role="client")
        Membership.objects.create(user=self.reviewer, business=self.business, role="accountant")
        Membership.objects.create(user=self.reviewer, business=self.other, role="accountant")
        self.invoice = create_upload(self.user, self.business.pk, image_upload())
        self.client.force_login(self.user)

    def url(self, name, invoice=None, business=None, **kwargs):
        args = {"business_id": (business or self.business).pk, **kwargs}
        if invoice is not None:
            args["pk"] = invoice.pk
        return reverse("invoices:" + name, kwargs=args)

    def fields(self, **kwargs):
        return {"supplier": "Fictional Supplier", "invoice_number": "A-123", "invoice_date": "2026-10-08",
                "due_date": "2026-10-20", "currency": "GBP", "total": "0.00", "notes": "", **kwargs}

    def save_and_submit(self, invoice=None):
        invoice = invoice or self.invoice
        self.assertEqual(self.client.post(self.url("edit", invoice), self.fields()).status_code, 302)
        self.assertEqual(self.client.post(self.url("transition", invoice, action="submit")).status_code, 302)

    def test_complete_manual_workflow(self):
        self.save_and_submit()
        self.assertEqual(self.client.post(self.url("edit", self.invoice), self.fields()).status_code, 403)
        self.client.force_login(self.reviewer)
        self.assertEqual(self.client.post(self.url("transition", self.invoice, action="return"), {"reason": "Fix number"}).status_code, 302)
        self.client.force_login(self.user)
        self.assertContains(self.client.get(self.invoice.get_absolute_url()), "Fix number")
        self.save_and_submit()
        self.client.force_login(self.reviewer)
        approve = self.url("transition", self.invoice, action="approve")
        self.assertEqual(self.client.post(approve).status_code, 302)
        self.assertEqual(self.client.post(approve).status_code, 400)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, "approved")
        self.assertEqual(self.invoice.approved_by, self.reviewer)
        self.assertEqual(self.invoice.events.filter(event_type="approve").count(), 1)
        rows = list(csv.reader(StringIO(self.client.get(self.url("export")).content.decode())))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][5], "0.00")

    def test_cross_business_urls_and_posted_business_cannot_grant_access(self):
        other_invoice = create_upload(self.other_user, self.other.pk, image_upload(colour="red"))
        for name in ["dashboard", "list", "upload", "export"]:
            self.assertEqual(self.client.get(self.url(name, business=self.other)).status_code, 404)
        for name in ["detail", "edit", "source", "delete"]:
            self.assertEqual(self.client.get(self.url(name, other_invoice, business=self.other)).status_code, 404)
            self.assertEqual(self.client.get(self.url(name, other_invoice)).status_code, 404)
        self.assertEqual(self.client.post(self.url("transition", other_invoice, business=self.other, action="approve")).status_code, 404)
        self.client.post(self.url("edit", self.invoice), {**self.fields(), "business": self.other.pk})
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.business_id, self.business.pk)

    def test_removed_membership_and_inactive_business_revoke_access(self):
        Membership.objects.filter(user=self.user).delete()
        self.assertEqual(self.client.get(self.invoice.get_absolute_url()).status_code, 404)
        self.assertEqual(self.client.get(self.url("source", self.invoice)).status_code, 404)
        self.assertContains(self.client.get(reverse("business_select")), "do not currently have access")
        self.client.force_login(self.reviewer)
        self.business.active = False
        self.business.save()
        self.assertEqual(self.client.get(self.invoice.get_absolute_url()).status_code, 404)

    def test_client_and_staff_cannot_review_export_or_use_admin(self):
        self.save_and_submit()
        self.assertEqual(self.client.post(self.url("transition", self.invoice, action="approve")).status_code, 403)
        self.assertEqual(self.client.get(self.url("export")).status_code, 403)
        self.user.is_staff = True
        self.user.save()
        self.assertEqual(self.client.post(self.url("transition", self.invoice, action="return"), {"reason": "No"}).status_code, 403)
        self.assertEqual(self.client.get("/admin/").status_code, 302)

    def test_authentication_logout_password_and_safe_next(self):
        self.client.logout()
        for url in [self.invoice.get_absolute_url(), self.url("source", self.invoice), reverse("password_change")]:
            self.assertEqual(self.client.get(url).status_code, 302)
        login = reverse("login")
        self.assertContains(self.client.post(login, {"username": "client", "password": "wrong"}), "Please enter a correct")
        response = self.client.post(login, {"username": "client", "password": "Testing-local-947!", "next": "https://example.com/"})
        self.assertEqual(response.url, reverse("business_select"))
        response = self.client.post(reverse("password_change"), {"old_password": "Testing-local-947!", "new_password1": "Changed-test-478!", "new_password2": "Changed-test-478!"})
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Changed-test-478!"))
        self.assertEqual(self.client.get(reverse("logout")).status_code, 405)
        self.assertEqual(self.client.post(reverse("logout")).status_code, 302)
        self.assertEqual(self.client.get(self.url("source", self.invoice)).status_code, 302)
        self.user.is_active = False
        self.user.save()
        self.client.post(login, {"username": "client", "password": "Changed-test-478!"})
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_csrf_required_for_mutations(self):
        browser = Client(enforce_csrf_checks=True)
        browser.force_login(self.user)
        for url in [reverse("logout"), self.url("upload"), self.url("edit", self.invoice), self.url("delete", self.invoice), self.url("transition", self.invoice, action="submit")]:
            self.assertEqual(browser.post(url).status_code, 403)

    def test_valid_formats_and_protected_response(self):
        for upload in [image_upload("photo.jpg", "red", "JPEG"), image_upload(colour="blue"), pdf_upload()]:
            self.assertEqual(self.client.post(self.url("upload"), {"document": upload}).status_code, 302)
        pdf = Invoice.objects.get(content_type="application/pdf")
        response = self.client.get(self.url("source", pdf))
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response["Content-Disposition"].startswith("attachment"))
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        response.close()
        self.assertEqual(self.client.get("/media/" + self.invoice.source_file.name).status_code, 404)

    def test_invalid_uploads_leave_no_records_or_files(self):
        before = set(Path(self.temp.name).rglob("*"))
        invalid = [SimpleUploadedFile("fake.png", b"not an image"), SimpleUploadedFile("fake.pdf", b"%PDF-broken"),
                   SimpleUploadedFile("image.exe", b"bad"), image_upload("disguised.jpg"), pdf_upload(encrypted=True),
                   pdf_upload(pages=11), SimpleUploadedFile("huge.png", b"x" * (10 * 1024 * 1024 + 1))]
        for upload in invalid:
            response = self.client.post(self.url("upload"), {"document": upload})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["form"].errors)
        self.assertEqual(Invoice.objects.count(), 1)
        self.assertEqual(set(Path(self.temp.name).rglob("*")), before)

    def test_image_decompression_dimensions_rejected(self):
        with patch("invoices.services.MAX_IMAGE_PIXELS", 10):
            with self.assertRaises(ValidationError):
                validate_document(image_upload())

    def test_upload_failure_cleans_saved_file(self):
        before = set(Path(self.temp.name).rglob("*"))
        with patch("invoices.services.event", side_effect=RuntimeError("failed event")):
            with self.assertRaises(RuntimeError):
                create_upload(self.user, self.business.pk, image_upload(colour="pink"))
        self.assertEqual(Invoice.objects.count(), 1)
        self.assertEqual(set(Path(self.temp.name).rglob("*")), before)

    def test_identical_uploads_scoped_and_database_constraint(self):
        response = self.client.post(self.url("upload"), {"document": image_upload()})
        self.assertContains(response, "Open existing invoice")
        create_upload(self.other_user, self.other.pk, image_upload())
        self.assertEqual(Invoice.objects.count(), 2)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Invoice.objects.create(business=self.business, file_hash=self.invoice.file_hash, uploaded_by=self.user)

    def test_duplicate_race_cleans_losing_file(self):
        # Simulate a simultaneous winner after the initial duplicate lookup.
        before = set(Path(self.temp.name).rglob("*"))
        from .services import DuplicateDocument
        with patch("invoices.services.Invoice.objects.filter") as query:
            query.return_value.first.side_effect = [None, self.invoice]
            with self.assertRaises(DuplicateDocument):
                create_upload(self.user, self.business.pk, image_upload())
        self.assertEqual(Invoice.objects.count(), 1)
        self.assertEqual(set(Path(self.temp.name).rglob("*")), before)

    def test_incomplete_drafts_invalid_amounts_and_dates(self):
        self.assertEqual(self.client.post(self.url("edit", self.invoice), {}).status_code, 302)
        self.assertEqual(self.client.post(self.url("transition", self.invoice, action="submit")).status_code, 400)
        for fields in [self.fields(total="-1.00"), self.fields(total="1.234"), self.fields(due_date="2026-10-01"), self.fields(invoice_date="bad")]:
            response = self.client.post(self.url("edit", self.invoice), fields)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["form"].errors)

    def test_normalised_duplicate_requires_acknowledgement(self):
        self.client.post(self.url("edit", self.invoice), self.fields())
        second = create_upload(self.user, self.business.pk, image_upload(colour="orange"))
        self.client.post(self.url("edit", second), self.fields(supplier="  fictional   SUPPLIER  ", invoice_number="a-123"))
        url = self.url("transition", second, action="submit")
        self.assertEqual(self.client.post(url).status_code, 400)
        self.assertEqual(second.events.filter(event_type="submit").count(), 0)
        self.assertEqual(self.client.post(url, {"acknowledge_duplicate": "on"}).status_code, 302)
        self.assertEqual(second.events.filter(event_type="duplicate_acknowledgement").count(), 1)

    def test_return_requires_reason_and_repeated_action_is_rejected(self):
        self.save_and_submit()
        self.client.force_login(self.reviewer)
        url = self.url("transition", self.invoice, action="return")
        self.assertEqual(self.client.post(url, {"reason": " "}).status_code, 400)
        self.assertEqual(self.client.post(url, {"reason": "Fix date"}).status_code, 302)
        self.assertEqual(self.client.post(url, {"reason": "Fix date"}).status_code, 400)
        self.assertEqual(self.invoice.events.filter(event_type="return").count(), 1)

    def test_draft_deletion_confirmation_and_file_cleanup(self):
        source = Path(self.invoice.source_file.path)
        self.assertEqual(self.client.post(self.url("delete", self.invoice)).status_code, 400)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.client.post(self.url("delete", self.invoice), {"confirm": "yes"}).status_code, 302)
        self.assertFalse(source.exists())
        self.assertFalse(Invoice.objects.filter(pk=self.invoice.pk).exists())

    def test_nondrafts_cannot_be_deleted(self):
        self.save_and_submit()
        self.assertEqual(self.client.post(self.url("delete", self.invoice), {"confirm": "yes"}).status_code, 403)
        self.assertTrue(Path(self.invoice.source_file.path).exists())

    def test_list_search_date_filters_pagination_and_invalid_filters(self):
        for i in range(22):
            Invoice.objects.create(business=self.business, uploaded_by=self.user, file_hash=f"{i:064x}",
                                   supplier="Fictional Search", invoice_number=f"SEARCH-{i}", invoice_date="2026-10-08")
        response = self.client.get(self.url("list"), {"q": "Search", "date_from": "2026-10-01"})
        self.assertEqual(len(response.context["page_obj"]), 20)
        self.assertContains(response, "q=Search")
        response = self.client.get(self.url("list"), {"q": "Search", "page": 2})
        self.assertEqual(len(response.context["page_obj"]), 2)
        self.assertEqual(len(self.client.get(self.url("list"), {"status": "approved"}).context["page_obj"]), 0)
        response = self.client.get(self.url("list"), {"date_from": "invalid"})
        self.assertTrue(response.context["filter_form"].errors)
        self.assertEqual(len(response.context["page_obj"]), 0)

    def test_export_approved_only_scoped_filtered_and_formula_safe(self):
        self.client.post(self.url("edit", self.invoice), self.fields(supplier=" =1+1", invoice_number="@evil"))
        self.client.post(self.url("transition", self.invoice, action="submit"))
        self.client.force_login(self.reviewer)
        self.client.post(self.url("transition", self.invoice, action="approve"))
        create_upload(self.user, self.business.pk, image_upload(colour="purple"))
        rows = list(csv.reader(StringIO(self.client.get(self.url("export"), {"q": "evil", "date_from": "2026-10-01"}).content.decode())))
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[1][0].startswith("'"))
        self.assertEqual(rows[1][1], "'@evil")
        rows = list(csv.reader(StringIO(self.client.get(self.url("export"), {"q": "absent"}).content.decode())))
        self.assertEqual(len(rows), 1)
        self.assertEqual(self.client.get(self.url("export"), {"date_from": "bad"}).status_code, 400)

    def test_demo_seed_is_idempotent_and_preserves_passwords(self):
        output = StringIO()
        call_command("seed_demo", password="Demo-testing-947!", stdout=output)
        count = Invoice.objects.count()
        user = get_user_model().objects.get(username="demo_accountant")
        original_password = user.password
        call_command("seed_demo", password="Different-testing-948!", stdout=output)
        user.refresh_from_db()
        self.assertEqual(user.password, original_password)
        self.assertEqual(Invoice.objects.count(), count)
        for name in ["Fictional Willow Studio", "Fictional Harbour Books"]:
            self.assertEqual(set(Invoice.objects.filter(business__name=name).values_list("status", flat=True)), set(Invoice.Status.values))
