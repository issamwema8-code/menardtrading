from django.contrib import admin
from .models import PurchaseOrder


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = ['po_number', 'customer', 'status', 'pickup_location', 'delivery_location', 'weight_tons', 'created_at']
    list_filter = ['status', 'created_at']
    search_fields = ['po_number', 'customer__company_name', 'raw_email_sender', 'cargo_description']
