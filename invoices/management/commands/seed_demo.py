from getpass import getpass
from io import BytesIO
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from PIL import Image, ImageDraw
from accounts.models import Business, Membership
from invoices.models import Invoice
from invoices.services import create_upload, edit_invoice, transition_invoice


class Command(BaseCommand):
    help = "Seed fictional accounts and records without changing existing passwords."

    def add_arguments(self, parser):
        parser.add_argument("--password", help="Password for new users; omitted to prompt securely.")

    def handle(self, *args, **options):
        User = get_user_model()
        names = ["demo_client_one", "demo_client_two", "demo_accountant"]
        password = options["password"]
        if any(not User.objects.filter(username=name).exists() for name in names):
            password = password or getpass("Password for new demo accounts: ")
            try:
                for name in names:
                    validate_password(password, User(username=name))
            except ValidationError as exc:
                raise CommandError(" ".join(exc.messages)) from exc
        users = []
        for name in names:
            user, created = User.objects.get_or_create(username=name)
            if created:
                user.set_password(password)
                user.save()
            users.append(user)
        for index, name in enumerate(["Fictional Willow Studio", "Fictional Harbour Books"]):
            business, _ = Business.objects.get_or_create(name=name)
            Membership.objects.get_or_create(user=users[index], business=business, defaults={"role": "client"})
            Membership.objects.get_or_create(user=users[2], business=business, defaults={"role": "accountant"})
            for state_index, state in enumerate(Invoice.Status.values):
                filename = f"demo-{index}-{state}.png"
                if Invoice.objects.filter(business=business, original_filename=filename).exists():
                    continue
                image = Image.new("RGB", (600, 240), (240 - state_index * 10, 245 - index * 10, 250))
                ImageDraw.Draw(image).text((30, 30), f"FICTIONAL DEMO ONLY\n{name}\n{state} sample {index}", fill="black")
                output = BytesIO()
                image.save(output, format="PNG")
                invoice = create_upload(users[index], business.pk, SimpleUploadedFile(filename, output.getvalue(), content_type="image/png"))
                edit_invoice(users[index], business.pk, invoice.pk, {
                    "supplier": "Fictional Paper Supplies", "invoice_number": f"DEMO-{index}-{state_index}",
                    "invoice_date": timezone.localdate().isoformat(), "currency": "GBP",
                    "total": str(25 + state_index), "notes": "Fictional demonstration record."})
                if state != Invoice.Status.DRAFT:
                    transition_invoice(users[index], business.pk, invoice.pk, "submit")
                if state == Invoice.Status.RETURNED:
                    transition_invoice(users[2], business.pk, invoice.pk, "return", reason="Please verify the supplier invoice number.")
                elif state == Invoice.Status.APPROVED:
                    transition_invoice(users[2], business.pk, invoice.pk, "approve")
        self.stdout.write(self.style.SUCCESS("Demo ready: two businesses, two clients and demo_accountant. Existing passwords preserved."))
