from django.contrib import admin, messages
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
    actions = ['recalculate_balances']

    @admin.action(description="Recalculate Totals, Receipts & Balances for Selected Invoices")
    def recalculate_balances(self, request, queryset):
        count = 0
        for inv in queryset:
            inv.recalculate_totals()
            count += 1
        self.message_user(request, f"Successfully recalculated totals, payments, and balances for {count} invoice(s).", messages.SUCCESS)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        form.instance.recalculate_totals()

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        obj.recalculate_totals()


@admin.register(PaymentReceipt)
class PaymentReceiptAdmin(admin.ModelAdmin):
    list_display = ['receipt_number', 'invoice', 'customer', 'amount_paid', 'payment_method', 'payment_date', 'transaction_reference']
    list_filter = ['payment_method', 'payment_date']
    search_fields = ['receipt_number', 'invoice__invoice_number', 'customer__company_name', 'transaction_reference']

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if obj.invoice:
            obj.invoice.recalculate_totals()

    def delete_model(self, request, obj):
        inv = obj.invoice
        super().delete_model(request, obj)
        if inv:
            inv.recalculate_totals()
