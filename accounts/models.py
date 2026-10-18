from django.conf import settings
from django.db import models


class Business(models.Model):
    name = models.CharField(max_length=200)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name", "pk"]
        verbose_name_plural = "businesses"

    def __str__(self):
        return self.name


class Membership(models.Model):
    class Role(models.TextChoices):
        CLIENT = "client", "Client"
        ACCOUNTANT = "accountant", "Accountant"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="memberships")
    business = models.ForeignKey(Business, on_delete=models.PROTECT, related_name="memberships")
    role = models.CharField(max_length=20, choices=Role.choices)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "business"], name="unique_business_membership")]

    def __str__(self):
        return f"{self.user} / {self.business} ({self.role})"
