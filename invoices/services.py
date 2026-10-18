"""Manual workflow boundary. Future extraction may suggest form values only."""
import hashlib
import warnings
from io import BytesIO
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

from accounts.services import accessible_business, require_reviewer
from .forms import InvoiceForm
from .models import Invoice, InvoiceEvent

MAX_UPLOAD_SIZE = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000


class DuplicateDocument(ValidationError):
    def __init__(self, invoice):
        self.invoice = invoice
        super().__init__("This document has already been uploaded to this business.")


def validate_document(upload):
    if upload.size > MAX_UPLOAD_SIZE:
        raise ValidationError("Document must be no larger than 10 MiB.")
    suffix = Path(upload.name).suffix.lower()
    if suffix not in {".pdf", ".jpg", ".jpeg", ".png"}:
        raise ValidationError("Choose a PDF, JPEG or PNG file.")
    upload.seek(0)
    data = upload.read(MAX_UPLOAD_SIZE + 1)
    upload.seek(0)
    if len(data) > MAX_UPLOAD_SIZE:
        raise ValidationError("Document must be no larger than 10 MiB.")
    if suffix == ".pdf":
        try:
            if not data.startswith(b"%PDF-"):
                raise ValueError("Invalid header")
            reader = PdfReader(BytesIO(data), strict=True)
            if reader.is_encrypted:
                raise ValidationError("Encrypted PDFs are not supported.")
            if not 1 <= len(reader.pages) <= 10:
                raise ValidationError("PDF must contain between 1 and 10 pages.")
            for page in reader.pages:
                # Read content streams as well as the page tree to catch malformed documents.
                page.get_contents()
                _ = page.mediabox
        except ValidationError:
            raise
        except Exception as exc:
            raise ValidationError("PDF could not be read. Upload a valid, unencrypted PDF.") from exc
        content_type = "application/pdf"
    else:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(data)) as image:
                    expected = "PNG" if suffix == ".png" else "JPEG"
                    if image.format != expected or image.width * image.height > MAX_IMAGE_PIXELS:
                        raise ValueError("Unsupported format or excessive dimensions")
                    image.verify()
                with Image.open(BytesIO(data)) as image:
                    if getattr(image, "n_frames", 1) != 1:
                        raise ValueError("Animated images are unsupported")
                    image.load()
        except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise ValidationError("Upload a valid, single-frame JPEG or PNG of at most 25 million pixels.") from exc
        content_type = "image/png" if suffix == ".png" else "image/jpeg"
    return hashlib.sha256(data).hexdigest(), content_type


def event(invoice, actor, kind, old="", new="", message=""):
    InvoiceEvent.objects.create(invoice=invoice, actor=actor, event_type=kind,
                                old_status=old, new_status=new, message=message)


def create_upload(user, business_id, upload):
    business = accessible_business(user, business_id)
    digest, content_type = validate_document(upload)
    existing = Invoice.objects.filter(business=business, file_hash=digest).first()
    if existing:
        raise DuplicateDocument(existing)
    invoice = Invoice(business=business, uploaded_by=user, file_hash=digest, content_type=content_type,
                      original_filename=Path(upload.name.replace("\\", "/")).name[:255])
    try:
        with transaction.atomic():
            invoice.source_file.save(upload.name, upload, save=False)
            invoice.save()
            event(invoice, user, "upload", new=Invoice.Status.DRAFT)
    except Exception as exc:
        if invoice.source_file.name:
            invoice.source_file.storage.delete(invoice.source_file.name)
        if isinstance(exc, IntegrityError):
            existing = Invoice.objects.filter(business=business, file_hash=digest).first()
            if existing:
                raise DuplicateDocument(existing) from exc
        raise
    return invoice


def locked_invoice(user, business_id, pk):
    business = accessible_business(user, business_id)
    return get_object_or_404(Invoice.objects.select_for_update(), business=business, pk=pk)


@transaction.atomic
def edit_invoice(user, business_id, pk, data):
    invoice = locked_invoice(user, business_id, pk)
    if not invoice.editable:
        raise ValidationError("Only draft and returned invoices can be edited.")
    form = InvoiceForm(data, instance=invoice)
    if not form.is_valid():
        return invoice, form
    if form.has_changed():
        form.save()
        event(invoice, user, "metadata_edit", message="Updated fields: " + ", ".join(form.changed_data))
    return invoice, form


def normalise(value):
    return " ".join(value.split()).casefold()


def possible_duplicates(invoice):
    if not invoice.supplier or not invoice.invoice_number:
        return []
    return [other for other in Invoice.objects.filter(business_id=invoice.business_id).exclude(pk=invoice.pk)
            if normalise(other.supplier) == normalise(invoice.supplier)
            and normalise(other.invoice_number) == normalise(invoice.invoice_number)]


def submission_errors(invoice):
    errors = []
    for field in ["supplier", "invoice_number", "invoice_date", "currency", "total"]:
        value = getattr(invoice, field)
        if value is None or (isinstance(value, str) and not value.strip()):
            errors.append(f"{field.replace('_', ' ').capitalize()} is required before submission.")
    try:
        invoice.full_clean()
    except ValidationError as exc:
        errors.extend(exc.messages)
    if invoice.invoice_date and invoice.due_date and invoice.due_date < invoice.invoice_date:
        errors.append("Due date cannot precede invoice date.")
    return errors


@transaction.atomic
def transition_invoice(user, business_id, pk, action, reason="", acknowledge=False):
    invoice = locked_invoice(user, business_id, pk)
    old = invoice.status
    if action in {"approve", "return"}:
        require_reviewer(user, invoice.business)
        if old != Invoice.Status.SUBMITTED:
            raise ValidationError("Only submitted invoices can be reviewed.")
        if action == "return" and not reason.strip():
            raise ValidationError("A return reason is required.")
        new = Invoice.Status.APPROVED if action == "approve" else Invoice.Status.RETURNED
    elif action == "submit":
        if not invoice.editable:
            raise ValidationError("Only draft and returned invoices can be submitted.")
        errors = submission_errors(invoice)
        if errors:
            raise ValidationError(errors)
        duplicates = possible_duplicates(invoice)
        if duplicates and not acknowledge:
            raise ValidationError("Possible duplicate found. Review and explicitly acknowledge it before submitting.")
        new = Invoice.Status.SUBMITTED
    else:
        raise ValidationError("Unknown action.")
    updates = {"status": new, "updated_at": timezone.now()}
    if action == "approve":
        updates.update(approved_by=user, approved_at=timezone.now())
    elif action == "return":
        updates["return_reason"] = reason.strip()
    # Conditional update also guards against stale/repeated transitions.
    if Invoice.objects.filter(pk=invoice.pk, status=old).update(**updates) != 1:
        raise ValidationError("Invoice changed during this action. Reload and try again.")
    if action == "submit" and duplicates:
        event(invoice, user, "duplicate_acknowledgement", message="Possible duplicates: " + ", ".join(str(x.pk) for x in duplicates))
    event(invoice, user, action, old, new, reason.strip() if action == "return" else "")
    invoice.refresh_from_db()
    return invoice


@transaction.atomic
def delete_draft(user, business_id, pk):
    invoice = locked_invoice(user, business_id, pk)
    if invoice.status != Invoice.Status.DRAFT:
        raise ValidationError("Only draft invoices can be deleted.")
    storage, name = invoice.source_file.storage, invoice.source_file.name
    invoice.delete()
    transaction.on_commit(lambda: storage.delete(name))


def csv_safe(value):
    text = "" if value is None else str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text
