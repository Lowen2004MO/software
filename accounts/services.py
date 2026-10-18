from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404
from .models import Business, Membership


def permitted_businesses(user):
    businesses = Business.objects.filter(active=True)
    if not user.is_active:
        return businesses.none()
    if user.is_superuser:
        return businesses
    return businesses.filter(memberships__user=user).distinct()


def accessible_business(user, business_id):
    return get_object_or_404(permitted_businesses(user), pk=business_id)


def can_review(user, business):
    return user.is_active and (user.is_superuser or Membership.objects.filter(
        user=user, business=business, role=Membership.Role.ACCOUNTANT).exists())


def require_reviewer(user, business):
    if not can_review(user, business):
        raise PermissionDenied("Only assigned accountants can review or export invoices.")
