from decimal import Decimal
from django.shortcuts import redirect
from django.views import View
from django.contrib import messages
from apps.accounts.permissions import PermissionRequiredMixin
from apps.customers.models import Customer


class CreateCustomerView(PermissionRequiredMixin, View):
    """
    Creates or updates a real customer profile.
    """
    permission_required = 'customers.create'
    def post(self, request):
        company_name = request.POST.get('company_name', '').strip()
        contact_name = request.POST.get('contact_name', '').strip()
        email = request.POST.get('email', '').strip()
        phone = request.POST.get('phone', '').strip()
        vat_number = request.POST.get('vat_number', '').strip()
        physical_address = request.POST.get('physical_address', '').strip()
        payment_terms = request.POST.get('payment_terms', Customer.PaymentTerms.DEPOSIT_50_POD_50)
        credit_limit = Decimal(request.POST.get('credit_limit', '0.00'))

        if not company_name or not email:
            messages.error(request, "Company name and email address are required.")
            return redirect('customers_list')

        customer, created = Customer.objects.get_or_create(
            email__iexact=email,
            defaults={
                'company_name': company_name,
                'contact_name': contact_name,
                'email': email,
                'phone': phone,
                'vat_number': vat_number,
                'physical_address': physical_address or 'N/A',
                'payment_terms': payment_terms,
                'credit_limit': credit_limit,
            }
        )

        if not created:
            customer.company_name = company_name
            customer.contact_name = contact_name
            customer.phone = phone
            customer.vat_number = vat_number
            customer.physical_address = physical_address or customer.physical_address
            customer.payment_terms = payment_terms
            customer.credit_limit = credit_limit
            customer.save()
            messages.success(request, f"Updated customer profile for {company_name}.")
        else:
            messages.success(request, f"Registered new customer {company_name} ({email}).")

        return redirect('customers_list')
