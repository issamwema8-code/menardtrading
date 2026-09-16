from django.contrib import admin
from .models import ExpenseCategory, Vendor, Expense, SupplierBill, SupplierBillPayment


@admin.register(ExpenseCategory)
class ExpenseCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'is_active', 'is_system', 'created_at')
    search_fields = ('name', 'code')
    list_filter = ('is_active', 'is_system')


@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    list_display = ('name', 'contact_name', 'email', 'phone', 'vat_number', 'is_active')
    search_fields = ('name', 'contact_name', 'email', 'vat_number')
    list_filter = ('is_active',)


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ('expense_number', 'date', 'category', 'vendor', 'payee', 'total_amount', 'payment_method', 'status')
    search_fields = ('expense_number', 'payee', 'description', 'vendor__name')
    list_filter = ('status', 'category', 'payment_method', 'date')


@admin.register(SupplierBill)
class SupplierBillAdmin(admin.ModelAdmin):
    list_display = ('bill_number', 'vendor', 'issue_date', 'due_date', 'total_amount', 'amount_paid', 'balance_due', 'status')
    search_fields = ('bill_number', 'supplier_reference', 'vendor__name')
    list_filter = ('status', 'due_date')


@admin.register(SupplierBillPayment)
class SupplierBillPaymentAdmin(admin.ModelAdmin):
    list_display = ('payment_number', 'bill', 'amount_paid', 'payment_date', 'payment_method', 'transaction_reference')
    search_fields = ('payment_number', 'bill__bill_number', 'transaction_reference')
    list_filter = ('payment_method', 'payment_date')
