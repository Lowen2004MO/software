from decimal import Decimal
from pathlib import Path
from uuid import uuid4
from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.urls import reverse


def source_path(instance, filename):
    return f"documents/{uuid4().hex}{Path(filename).suffix.lower()}"


class Invoice(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        RETURNED = "returned", "Returned"
        APPROVED = "approved", "Approved"

    CURRENCIES = [("GBP", "GBP"), ("EUR", "EUR"), ("USD", "USD")]
    business = models.ForeignKey("accounts.Business", on_delete=models.PROTECT, related_name="invoices")
    source_file = models.FileField(upload_to=source_path)
    original_filename = models.CharField(max_length=255)
    file_hash = models.CharField(max_length=64)
    content_type = models.CharField(max_length=30)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="uploaded_invoices")
    supplier = models.CharField(max_length=200, blank=True)
    invoice_number = models.CharField(max_length=100, blank=True)
    invoice_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    currency = models.CharField(max_length=3, choices=CURRENCIES, blank=True)
    total = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(Decimal("0"))])
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    return_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="approved_invoices", null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint(fields=["business", "file_hash"], name="unique_business_document"),
            models.CheckConstraint(condition=models.Q(total__gte=0) | models.Q(total__isnull=True), name="invoice_total_nonnegative"),
        ]

    @property
    def editable(self):
        return self.status in [self.Status.DRAFT, self.Status.RETURNED]

    def get_absolute_url(self):
        return reverse("invoices:detail", kwargs={"business_id": self.business_id, "pk": self.pk})

    def __str__(self):
        return self.invoice_number or f"Invoice {self.pk}"


class InvoiceEvent(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    event_type = models.CharField(max_length=30)
    old_status = models.CharField(max_length=12, blank=True)
    new_status = models.CharField(max_length=12, blank=True)
    message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]
