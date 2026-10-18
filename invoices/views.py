import csv
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from accounts.services import accessible_business, can_review, require_reviewer
from .forms import FilterForm, InvoiceForm, ReturnForm, SubmitForm, UploadForm
from .models import Invoice
from .services import (DuplicateDocument, create_upload, csv_safe, delete_draft, edit_invoice,
                       possible_duplicates, transition_invoice)


def context(request, business):
    return {"business": business, "can_review": can_review(request.user, business)}


def scoped_invoice(request, business_id, pk):
    business = accessible_business(request.user, business_id)
    return business, get_object_or_404(Invoice, business=business, pk=pk)


def filtered_invoices(business, data, export=False):
    form = FilterForm(data)
    records = Invoice.objects.filter(business=business)
    if form.is_valid():
        filters = form.cleaned_data
        if filters.get("q"):
            records = records.filter(Q(supplier__icontains=filters["q"]) | Q(invoice_number__icontains=filters["q"]))
        if filters.get("status") and not export:
            records = records.filter(status=filters["status"])
        if filters.get("date_from"):
            records = records.filter(invoice_date__gte=filters["date_from"])
        if filters.get("date_to"):
            records = records.filter(invoice_date__lte=filters["date_to"])
    else:
        records = records.none()
    return records, form


@login_required
def dashboard(request, business_id):
    business = accessible_business(request.user, business_id)
    counts = dict(Invoice.objects.filter(business=business).values("status").annotate(count=Count("pk")).values_list("status", "count"))
    return render(request, "invoices/dashboard.html", {**context(request, business),
        "counts": [(label, counts.get(status, 0)) for status, label in Invoice.Status.choices],
        "recent": Invoice.objects.filter(business=business)[:8]})


@login_required
def invoice_list(request, business_id):
    business = accessible_business(request.user, business_id)
    records, form = filtered_invoices(business, request.GET)
    query = request.GET.copy()
    query.pop("page", None)
    return render(request, "invoices/list.html", {**context(request, business), "filter_form": form,
        "page_obj": Paginator(records, 20).get_page(request.GET.get("page")), "filter_query": query.urlencode()})


@login_required
def upload(request, business_id):
    business = accessible_business(request.user, business_id)
    form = UploadForm(request.POST if request.method == "POST" else None,
                      request.FILES if request.method == "POST" else None)
    duplicate = None
    if request.method == "POST" and form.is_valid():
        try:
            invoice = create_upload(request.user, business.pk, form.cleaned_data["document"])
        except DuplicateDocument as exc:
            duplicate = exc.invoice
            form.add_error("document", exc)
        except ValidationError as exc:
            form.add_error("document", exc)
        else:
            messages.success(request, "Document uploaded. Enter its invoice details manually.")
            return redirect("invoices:edit", business_id=business.pk, pk=invoice.pk)
    return render(request, "invoices/upload.html", {**context(request, business), "form": form, "duplicate": duplicate})


@login_required
def detail(request, business_id, pk):
    business, invoice = scoped_invoice(request, business_id, pk)
    return render(request, "invoices/detail.html", {**context(request, business), "invoice": invoice,
        "duplicates": possible_duplicates(invoice) if invoice.editable else [], "submit_form": SubmitForm(), "return_form": ReturnForm()})


@login_required
def edit(request, business_id, pk):
    business, invoice = scoped_invoice(request, business_id, pk)
    if not invoice.editable:
        raise PermissionDenied("Submitted and approved invoices are read-only.")
    form = InvoiceForm(instance=invoice)
    if request.method == "POST":
        try:
            invoice, form = edit_invoice(request.user, business_id, pk, request.POST)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return redirect(invoice)
        if form.is_valid():
            messages.success(request, "Invoice details saved.")
            return redirect(invoice)
    return render(request, "invoices/edit.html", {**context(request, business), "invoice": invoice, "form": form})


@login_required
def source(request, business_id, pk):
    business, invoice = scoped_invoice(request, business_id, pk)
    try:
        handle = invoice.source_file.open("rb")
    except FileNotFoundError as exc:
        raise Http404("Document is unavailable.") from exc
    response = FileResponse(handle, content_type=invoice.content_type, filename=invoice.original_filename,
                            as_attachment=invoice.content_type == "application/pdf" or request.GET.get("download") == "1")
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
@require_POST
def transition(request, business_id, pk, action):
    business, invoice = scoped_invoice(request, business_id, pk)
    if action in {"approve", "return"}:
        require_reviewer(request.user, business)
    form = ReturnForm(request.POST) if action == "return" else SubmitForm(request.POST)
    if not form.is_valid():
        return render(request, "invoices/action_error.html", {**context(request, business), "invoice": invoice, "errors": form.errors}, status=400)
    try:
        invoice = transition_invoice(request.user, business_id, pk, action,
            reason=form.cleaned_data.get("reason", ""), acknowledge=form.cleaned_data.get("acknowledge_duplicate", False))
    except ValidationError as exc:
        return render(request, "invoices/action_error.html", {**context(request, business), "invoice": invoice, "errors": exc.messages}, status=400)
    messages.success(request, f"Invoice {invoice.get_status_display().lower()}.")
    return redirect(invoice)


@login_required
def delete(request, business_id, pk):
    business, invoice = scoped_invoice(request, business_id, pk)
    if invoice.status != Invoice.Status.DRAFT:
        raise PermissionDenied("Only drafts can be deleted.")
    if request.method == "POST":
        if request.POST.get("confirm") != "yes":
            return HttpResponse("Deletion confirmation is required.", status=400)
        try:
            delete_draft(request.user, business_id, pk)
        except ValidationError as exc:
            return HttpResponse(" ".join(exc.messages), status=400)
        messages.success(request, "Draft and source document deleted.")
        return redirect("invoices:list", business_id=business_id)
    return render(request, "invoices/delete.html", {**context(request, business), "invoice": invoice})


@login_required
def export(request, business_id):
    business = accessible_business(request.user, business_id)
    require_reviewer(request.user, business)
    records, form = filtered_invoices(business, request.GET, export=True)
    if not form.is_valid():
        return HttpResponse("Invalid export filters.", status=400)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="approved-invoices.csv"'
    response["Cache-Control"] = "private, no-store"
    writer = csv.writer(response)
    writer.writerow(["supplier", "invoice_number", "invoice_date", "due_date", "currency", "total", "approved_at"])
    for invoice in records.filter(status=Invoice.Status.APPROVED):
        writer.writerow([csv_safe(invoice.supplier), csv_safe(invoice.invoice_number), invoice.invoice_date,
                         invoice.due_date or "", invoice.currency, f"{invoice.total:.2f}", invoice.approved_at.isoformat()])
    return response
