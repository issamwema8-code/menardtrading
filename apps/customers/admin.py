from django.contrib import admin
from .models import Customer


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ['company_name', 'contact_name', 'email', 'phone', 'payment_terms', 'vat_number', 'is_active', 'created_at']
    list_filter = ['payment_terms', 'is_active', 'created_at']
    search_fields = ['company_name', 'contact_name', 'email', 'phone', 'vat_number']
