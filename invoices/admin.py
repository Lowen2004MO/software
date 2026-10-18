from django.contrib import admin
from .models import Invoice, InvoiceEvent


class ReadOnlyHistory(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Invoice)
class InvoiceAdmin(ReadOnlyHistory):
    list_display = ["id", "business", "supplier", "invoice_number", "status", "created_at"]
    list_filter = ["business", "status"]
    exclude = ["source_file"]


@admin.register(InvoiceEvent)
class InvoiceEventAdmin(ReadOnlyHistory):
    list_display = ["invoice", "actor", "event_type", "created_at"]
