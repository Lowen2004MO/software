from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from .services import permitted_businesses


@login_required
def business_select(request):
    businesses = permitted_businesses(request.user)
    if businesses.count() == 1:
        return redirect("invoices:dashboard", business_id=businesses.first().pk)
    return render(request, "accounts/business_select.html", {"businesses": businesses})
