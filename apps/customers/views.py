import json
from decimal import Decimal
from django.shortcuts import redirect
from django.views import View
from django.http import JsonResponse
from django.db import models
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
        billing_address = request.POST.get('billing_address', '').strip()
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
                'billing_address': billing_address,
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
            if billing_address:
                customer.billing_address = billing_address
            customer.payment_terms = payment_terms
            customer.credit_limit = credit_limit
            customer.save()
            messages.success(request, f"Updated client profile for {company_name}.")
        else:
            messages.success(request, f"Registered new client {company_name} ({email}).")

        return redirect('customers_list')


class CustomerSearchAPIView(PermissionRequiredMixin, View):
    """
    Search endpoint for searchable customer dropdown / combobox.
    Supports filtering across company name, contact, email, phone, and trading name.
    """
    permission_required = ('customers.view', 'quotations.create', 'orders.create')
    match_all_permissions = False

    def get(self, request):
        query = request.GET.get('q', '').strip()
        qs = Customer.objects.filter(is_active=True)
        if query:
            qs = qs.filter(
                models.Q(company_name__icontains=query) |
                models.Q(contact_name__icontains=query) |
                models.Q(email__icontains=query) |
                models.Q(phone__icontains=query) |
                models.Q(trading_name__icontains=query)
            )
        customers = [
            {
                'id': c.id,
                'company_name': c.company_name,
                'contact_name': c.contact_name,
                'email': c.email,
                'phone': c.phone,
                'payment_terms': c.payment_terms,
                'payment_terms_display': c.get_payment_terms_display(),
            }
            for c in qs.order_by('company_name')[:100]
        ]
        return JsonResponse({'results': customers, 'count': len(customers)})


class QuickCreateCustomerView(PermissionRequiredMixin, View):
    """
    AJAX endpoint to quickly register a new customer from within modals (e.g. Quotation creation).
    Returns created customer record in JSON format for instant dropdown selection.
    """
    permission_required = 'customers.create'

    def post(self, request):
        if request.content_type == 'application/json':
            try:
                data = json.loads(request.body.decode('utf-8'))
            except Exception:
                data = {}
        else:
            data = request.POST

        company_name = data.get('company_name', '').strip()
        contact_name = data.get('contact_name', '').strip() or company_name
        email = data.get('email', '').strip()
        phone = data.get('phone', '').strip()
        vat_number = data.get('vat_number', '').strip()
        physical_address = data.get('physical_address', '').strip() or 'N/A'
        billing_address = data.get('billing_address', '').strip()
        payment_terms = data.get('payment_terms', Customer.PaymentTerms.DEPOSIT_50_POD_50)

        if not company_name:
            return JsonResponse({'success': False, 'error': 'Company name is required.'}, status=400)
        if not email:
            return JsonResponse({'success': False, 'error': 'Email address is required.'}, status=400)

        customer, created = Customer.objects.get_or_create(
            email__iexact=email,
            defaults={
                'company_name': company_name,
                'contact_name': contact_name,
                'email': email,
                'phone': phone,
                'vat_number': vat_number,
                'physical_address': physical_address,
                'billing_address': billing_address,
                'payment_terms': payment_terms,
            }
        )

        if not created:
            customer.company_name = company_name
            customer.contact_name = contact_name
            if phone:
                customer.phone = phone
            if vat_number:
                customer.vat_number = vat_number
            if physical_address and physical_address != 'N/A':
                customer.physical_address = physical_address
            if billing_address:
                customer.billing_address = billing_address
            if payment_terms:
                customer.payment_terms = payment_terms
            customer.save()

        return JsonResponse({
            'success': True,
            'created': created,
            'customer': {
                'id': customer.id,
                'company_name': customer.company_name,
                'contact_name': customer.contact_name,
                'email': customer.email,
                'phone': customer.phone,
                'payment_terms': customer.payment_terms,
                'payment_terms_display': customer.get_payment_terms_display(),
            },
            'message': f"Customer '{customer.company_name}' {'registered' if created else 'updated'} successfully."
        })


class CreatePaymentTermOptionView(PermissionRequiredMixin, View):
    """
    AJAX endpoint to dynamically create a new custom payment term on the fly.
    """
    permission_required = ('customers.create', 'customers.update')
    match_all_permissions = False

    def post(self, request):
        from apps.customers.models import PaymentTermOption
        if request.content_type == 'application/json':
            try:
                data = json.loads(request.body.decode('utf-8'))
            except Exception:
                data = {}
        else:
            data = request.POST

        name = data.get('name', '').strip()
        description = data.get('description', '').strip()

        if not name:
            return JsonResponse({'success': False, 'error': 'Payment term name is required.'}, status=400)

        try:
            term = PaymentTermOption.add_custom_term(name=name, description=description)
            return JsonResponse({
                'success': True,
                'term': {
                    'code': term.code,
                    'name': term.name,
                },
                'message': f"Payment term '{term.name}' added successfully."
            })
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)


class PaymentTermOptionsAPIView(PermissionRequiredMixin, View):
    """
    Returns list of all active payment term options.
    """
    permission_required = ('customers.view', 'quotations.create', 'orders.create')
    match_all_permissions = False

    def get(self, request):
        from apps.customers.models import PaymentTermOption
        terms = [
            {'code': t.code, 'name': t.name}
            for t in PaymentTermOption.get_all_terms()
        ]
        return JsonResponse({'terms': terms, 'count': len(terms)})


