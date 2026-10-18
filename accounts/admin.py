from django.contrib import admin
from .models import Business, Membership


class MembershipInline(admin.TabularInline):
    model = Membership
    extra = 0


@admin.register(Business)
class BusinessAdmin(admin.ModelAdmin):
    list_display = ["name", "active", "created_at"]
    inlines = [MembershipInline]


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ["user", "business", "role"]
    list_filter = ["business", "role"]
