from django.contrib import admin
from .models import Invoice, InvoiceLineItem, PaymentReceipt


class InvoiceLineItemInline(admin.TabularInline):
    model = InvoiceLineItem
    extra = 1


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ['invoice_number', 'customer', 'invoice_type', 'status', 'total_amount', 'amount_paid', 'balance_due', 'issue_date', 'due_date']
    list_filter = ['invoice_type', 'status', 'issue_date', 'due_date']
    search_fields = ['invoice_number', 'customer__company_name']
    inlines = [InvoiceLineItemInline]


@admin.register(PaymentReceipt)
class PaymentReceiptAdmin(admin.ModelAdmin):
    list_display = ['receipt_number', 'invoice', 'customer', 'amount_paid', 'payment_method', 'payment_date', 'transaction_reference']
    list_filter = ['payment_method', 'payment_date']
    search_fields = ['receipt_number', 'invoice__invoice_number', 'customer__company_name', 'transaction_reference']
