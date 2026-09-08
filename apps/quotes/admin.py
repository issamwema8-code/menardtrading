from django.contrib import admin
from .models import Quotation, QuoteLineItem


class QuoteLineItemInline(admin.TabularInline):
    model = QuoteLineItem
    extra = 1


@admin.register(Quotation)
class QuotationAdmin(admin.ModelAdmin):
    list_display = ['quote_number', 'customer', 'status', 'subtotal', 'vat_amount', 'total_amount', 'valid_until', 'created_at']
    list_filter = ['status', 'created_at', 'valid_until']
    search_fields = ['quote_number', 'customer__company_name']
    inlines = [QuoteLineItemInline]
