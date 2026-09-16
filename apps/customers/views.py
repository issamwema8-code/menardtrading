import json
from decimal import Decimal
from django.shortcuts import redirect, render, get_object_or_404
from django.views import View
from django.http import JsonResponse, HttpResponse
from django.db import models, transaction
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.contrib import messages
from apps.accounts.permissions import PermissionRequiredMixin
from apps.customers.models import Customer, PaymentTermOption


def is_ajax_request(request):
    return (
        request.headers.get('x-requested-with') == 'XMLHttpRequest'
        or 'application/json' in request.headers.get('Accept', '')
        or request.content_type == 'application/json'
        or request.GET.get('format') == 'json'
    )


class CustomersListView(PermissionRequiredMixin, View):
    """
    Client Accounts Directory: lists active or archived clients.
    Supports standard HTML rendering and JSON payload for dynamic async table updates.
    """
    permission_required = 'customers.view'

    def get(self, request):
        show_archived = request.GET.get('archived') == '1'
        customers_qs = Customer.objects.filter(is_active=not show_archived).order_by('company_name')

        # Optional query filter
        q = request.GET.get('q', '').strip()
        if q:
            customers_qs = customers_qs.filter(
                models.Q(company_name__icontains=q) |
                models.Q(contact_name__icontains=q) |
                models.Q(email__icontains=q) |
                models.Q(phone__icontains=q) |
                models.Q(vat_number__icontains=q) |
                models.Q(trading_name__icontains=q)
            )

        terms_filter = request.GET.get('terms', '').strip()
        if terms_filter and terms_filter != 'ALL':
            customers_qs = customers_qs.filter(payment_terms=terms_filter)

        payment_terms = PaymentTermOption.get_all_terms()
        active_count = Customer.objects.filter(is_active=True).count()
        archived_count = Customer.objects.filter(is_active=False).count()

        if is_ajax_request(request):
            return JsonResponse({
                'success': True,
                'count': customers_qs.count(),
                'active_count': active_count,
                'archived_count': archived_count,
                'customers': [c.to_dict() for c in customers_qs],
            })

        context = {
            'active_tab': 'customers',
            'customers': customers_qs,
            'show_archived': show_archived,
            'active_count': active_count,
            'archived_count': archived_count,
            'global_payment_terms': payment_terms,
        }
        return render(request, 'customers/customers_list.html', context)


class CreateCustomerView(PermissionRequiredMixin, View):
    """
    Creates a new client account profile with comprehensive validation,
    duplicate prevention, and immediate availability across the system.
    """
    permission_required = 'customers.create'

    def get(self, request):
        return render(request, 'customers/customer_form.html', {
            'customer': None,
            'payment_terms': PaymentTermOption.get_all_terms(),
            'active_tab': 'customers',
        })

    def post(self, request):
        is_json = request.content_type == 'application/json'
        if is_json:
            try:
                data = json.loads(request.body.decode('utf-8'))
            except Exception:
                return JsonResponse({'success': False, 'error': 'Invalid JSON format in request.'}, status=400)
        else:
            data = request.POST

        company_name = str(data.get('company_name', '')).strip()
        trading_name = str(data.get('trading_name', '')).strip()
        contact_name = str(data.get('contact_name', '')).strip() or company_name
        email = str(data.get('email', '')).strip()
        phone = str(data.get('phone', '')).strip()
        vat_number = str(data.get('vat_number', '')).strip()
        registration_number = str(data.get('registration_number', '')).strip()
        physical_address = str(data.get('physical_address', '')).strip() or 'N/A'
        billing_address = str(data.get('billing_address', '')).strip()
        payment_terms = str(data.get('payment_terms', Customer.PaymentTerms.DEPOSIT_50_POD_50)).strip()
        
        try:
            credit_limit = Decimal(str(data.get('credit_limit', '0.00') or '0.00').strip())
        except Exception:
            if is_json or is_ajax_request(request):
                return JsonResponse({'success': False, 'error': 'Credit limit must be a valid numeric amount.'}, status=400)
            messages.error(request, "Credit limit must be a valid numeric amount.")
            return redirect('customers_list')

        # Validation
        if not company_name:
            if is_json or is_ajax_request(request):
                return JsonResponse({'success': False, 'error': 'Company name is required.'}, status=400)
            messages.error(request, "Company name is required.")
            return redirect('customers_list')

        if not email:
            if is_json or is_ajax_request(request):
                return JsonResponse({'success': False, 'error': 'Email address is required.'}, status=400)
            messages.error(request, "Email address is required.")
            return redirect('customers_list')

        try:
            validate_email(email)
        except ValidationError:
            if is_json or is_ajax_request(request):
                return JsonResponse({'success': False, 'error': 'Please enter a valid email address.'}, status=400)
            messages.error(request, "Please enter a valid email address.")
            return redirect('customers_list')

        # Check for duplicate active client email
        existing_active = Customer.objects.filter(email__iexact=email, is_active=True).first()
        if existing_active:
            err_msg = f"An active client with this email address already exists ({existing_active.company_name})."
            if is_json or is_ajax_request(request):
                return JsonResponse({'success': False, 'error': err_msg}, status=409)
            messages.error(request, err_msg)
            return redirect('customers_list')

        # If an archived client with this email exists, reactivate and update or create new
        existing_archived = Customer.objects.filter(email__iexact=email, is_active=False).first()
        if existing_archived:
            with transaction.atomic():
                existing_archived.company_name = company_name
                existing_archived.trading_name = trading_name
                existing_archived.contact_name = contact_name
                existing_archived.phone = phone
                existing_archived.vat_number = vat_number
                existing_archived.registration_number = registration_number
                existing_archived.physical_address = physical_address
                existing_archived.billing_address = billing_address
                existing_archived.payment_terms = payment_terms
                existing_archived.credit_limit = credit_limit
                existing_archived.is_active = True
                existing_archived.save()
                customer = existing_archived
        else:
            with transaction.atomic():
                customer = Customer.objects.create(
                    company_name=company_name,
                    trading_name=trading_name,
                    contact_name=contact_name,
                    email=email,
                    phone=phone,
                    vat_number=vat_number,
                    registration_number=registration_number,
                    physical_address=physical_address,
                    billing_address=billing_address,
                    payment_terms=payment_terms,
                    credit_limit=credit_limit,
                    is_active=True,
                )

        if is_json or is_ajax_request(request):
            return JsonResponse({
                'success': True,
                'customer': customer.to_dict(),
                'message': f"Client '{customer.company_name}' registered successfully."
            }, status=201)

        messages.success(request, f"Registered new client {customer.company_name} ({customer.email}).")
        return redirect('customers_list')


class CustomerCreatePageView(PermissionRequiredMixin, View):
    """
    Standalone form page for adding a client (useful for direct bookmarking or fallback).
    """
    permission_required = 'customers.create'

    def get(self, request):
        return render(request, 'customers/customer_form.html', {
            'customer': None,
            'payment_terms': PaymentTermOption.get_all_terms(),
            'active_tab': 'customers',
        })


class CustomerUpdateView(PermissionRequiredMixin, View):
    """
    Edits an existing client's details.
    Preserves historical document snapshots (does not alter previously issued invoices/quotes).
    """
    permission_required = 'customers.update'

    def get(self, request, pk):
        customer = get_object_or_404(Customer, pk=pk)
        if is_ajax_request(request):
            return JsonResponse({'success': True, 'customer': customer.to_dict()})

        return render(request, 'customers/customer_form.html', {
            'customer': customer,
            'payment_terms': PaymentTermOption.get_all_terms(),
            'active_tab': 'customers',
        })

    def post(self, request, pk):
        customer = get_object_or_404(Customer, pk=pk)
        is_json = request.content_type == 'application/json'

        if is_json:
            try:
                data = json.loads(request.body.decode('utf-8'))
            except Exception:
                return JsonResponse({'success': False, 'error': 'Invalid JSON format in request.'}, status=400)
        else:
            data = request.POST

        email = str(data.get('email', '')).strip()
        company_name = str(data.get('company_name', '')).strip()
        contact_name = str(data.get('contact_name', '')).strip() or company_name

        if not company_name:
            err = "Company name is required."
            if is_json or is_ajax_request(request):
                return JsonResponse({'success': False, 'error': err}, status=400)
            messages.error(request, err)
            return redirect('edit_customer', pk=pk)

        if not email:
            err = "Email address is required."
            if is_json or is_ajax_request(request):
                return JsonResponse({'success': False, 'error': err}, status=400)
            messages.error(request, err)
            return redirect('edit_customer', pk=pk)

        try:
            validate_email(email)
        except ValidationError:
            err = "Please enter a valid email address."
            if is_json or is_ajax_request(request):
                return JsonResponse({'success': False, 'error': err}, status=400)
            messages.error(request, err)
            return redirect('edit_customer', pk=pk)

        if Customer.objects.filter(email__iexact=email, is_active=True).exclude(pk=pk).exists():
            err = "Another active client already uses this email address."
            if is_json or is_ajax_request(request):
                return JsonResponse({'success': False, 'error': err}, status=409)
            messages.error(request, err)
            return redirect('edit_customer', pk=pk)

        try:
            credit_limit = Decimal(str(data.get('credit_limit', '0.00') or '0.00').strip())
        except Exception:
            err = "Credit limit must be a valid numeric amount."
            if is_json or is_ajax_request(request):
                return JsonResponse({'success': False, 'error': err}, status=400)
            messages.error(request, err)
            return redirect('edit_customer', pk=pk)

        with transaction.atomic():
            customer.company_name = company_name
            customer.trading_name = str(data.get('trading_name', '')).strip()
            customer.contact_name = contact_name
            customer.email = email
            customer.phone = str(data.get('phone', '')).strip()
            customer.vat_number = str(data.get('vat_number', '')).strip()
            customer.registration_number = str(data.get('registration_number', '')).strip()
            customer.physical_address = str(data.get('physical_address', '')).strip() or 'N/A'
            customer.billing_address = str(data.get('billing_address', '')).strip()
            customer.payment_terms = str(data.get('payment_terms', customer.payment_terms or Customer.PaymentTerms.DEPOSIT_50_POD_50))
            customer.credit_limit = credit_limit
            customer.save()

        if is_json or is_ajax_request(request):
            return JsonResponse({
                'success': True,
                'customer': customer.to_dict(),
                'message': f"Client '{customer.company_name}' updated successfully."
            })

        messages.success(request, f"Client '{customer.company_name}' updated successfully.")
        return redirect('customer_detail', pk=pk)


class CustomerDetailView(PermissionRequiredMixin, View):
    """
    Displays full client profile alongside all related business activities:
    Quotations, Invoices, Outstanding balances, Receipts, Jobs/Loads, and Purchase Orders.
    Supports both HTML rendering and JSON payload for instant detail modal viewing.
    """
    permission_required = 'customers.view'

    def get(self, request, pk):
        customer = get_object_or_404(Customer, pk=pk)
        
        quotations = customer.quotations.select_related('purchase_order').order_by('-created_at')[:30]
        invoices = customer.invoices.select_related('job').prefetch_related('receipts').order_by('-created_at')[:30]
        receipts = customer.payment_receipts.select_related('invoice').order_by('-created_at')[:30]
        jobs = customer.logistics_jobs.order_by('-created_at')[:30]
        purchase_orders = customer.purchase_orders.order_by('-created_at')[:30]
        financial_summary = customer.get_financial_summary()

        if is_ajax_request(request):
            return JsonResponse({
                'success': True,
                'customer': customer.to_dict(),
                'financial_summary': {
                    'total_invoiced': float(financial_summary['total_invoiced']),
                    'total_paid': float(financial_summary['total_paid']),
                    'total_balance_due': float(financial_summary['total_balance_due']),
                    'credit_limit': float(financial_summary['credit_limit']),
                    'invoices_count': financial_summary['invoices_count'],
                    'unpaid_invoices_count': financial_summary['unpaid_invoices_count'],
                    'quotations_count': financial_summary['quotations_count'],
                    'receipts_count': financial_summary['receipts_count'],
                    'jobs_count': financial_summary['jobs_count'],
                    'purchase_orders_count': financial_summary['purchase_orders_count'],
                    'has_historical_records': financial_summary['has_historical_records'],
                },
                'quotations': [
                    {
                        'id': q.id,
                        'quote_number': q.quote_number,
                        'reference_number': q.reference_number or '',
                        'status': q.status,
                        'status_display': q.get_status_display(),
                        'total_amount': float(q.total_amount or 0),
                        'created_at': q.created_at.strftime('%Y-%m-%d') if q.created_at else '',
                    } for q in quotations
                ],
                'invoices': [
                    {
                        'id': inv.id,
                        'invoice_number': inv.invoice_number,
                        'reference_number': inv.reference_number or '',
                        'invoice_type': inv.invoice_type,
                        'invoice_type_display': inv.get_invoice_type_display(),
                        'status': inv.status,
                        'status_display': inv.get_status_display(),
                        'total_amount': float(inv.total_amount or 0),
                        'amount_paid': float(inv.amount_paid or 0),
                        'balance_due': float(inv.balance_due or 0),
                        'issue_date': inv.issue_date.strftime('%Y-%m-%d') if inv.issue_date else '',
                        'due_date': inv.due_date.strftime('%Y-%m-%d') if inv.due_date else '',
                    } for inv in invoices
                ],
                'receipts': [
                    {
                        'id': r.id,
                        'receipt_number': r.receipt_number,
                        'invoice_number': r.invoice.invoice_number if r.invoice else '',
                        'payment_method': r.payment_method,
                        'payment_method_display': r.get_payment_method_display(),
                        'amount_paid': float(r.amount_paid or 0),
                        'transaction_reference': r.transaction_reference or '',
                        'payment_date': r.payment_date.strftime('%Y-%m-%d') if r.payment_date else '',
                    } for r in receipts
                ],
                'jobs': [
                    {
                        'id': j.id,
                        'job_number': j.job_number,
                        'status': j.status,
                        'status_display': j.get_status_display(),
                        'driver_name': j.driver_name or '',
                        'vehicle_reg': j.vehicle_registration or '',
                        'created_at': j.created_at.strftime('%Y-%m-%d') if j.created_at else '',
                    } for j in jobs
                ],
                'purchase_orders': [
                    {
                        'id': po.id,
                        'po_number': po.po_number,
                        'status': po.status,
                        'status_display': po.get_status_display(),
                        'created_at': po.created_at.strftime('%Y-%m-%d') if po.created_at else '',
                    } for po in purchase_orders
                ],
            })

        return render(request, 'customers/customer_detail.html', {
            'customer': customer,
            'financial_summary': financial_summary,
            'quotations': quotations,
            'invoices': invoices,
            'receipts': receipts,
            'jobs': jobs,
            'purchase_orders': purchase_orders,
            'active_tab': 'customers',
        })


class ArchiveCustomerView(PermissionRequiredMixin, View):
    """
    Soft-deletes / Archives a client record.
    Preserves historical invoices, quotes, receipts, and jobs.
    """
    permission_required = 'customers.delete'

    def post(self, request, pk):
        customer = get_object_or_404(Customer, pk=pk)
        has_history = customer.has_historical_records()
        customer.archive()

        msg = f"Client '{customer.company_name}' was archived and all historical records were preserved."
        if is_ajax_request(request):
            return JsonResponse({
                'success': True,
                'action': 'archived',
                'has_historical_records': has_history,
                'message': msg,
                'customer': customer.to_dict(),
            })

        messages.success(request, msg)
        return redirect('customers_list')


class RestoreCustomerView(PermissionRequiredMixin, View):
    """
    Restores an archived customer back to active status.
    """
    permission_required = 'customers.update'

    def post(self, request, pk):
        customer = get_object_or_404(Customer, pk=pk)
        customer.restore()

        msg = f"Client '{customer.company_name}' has been successfully restored to active status."
        if is_ajax_request(request):
            return JsonResponse({
                'success': True,
                'action': 'restored',
                'message': msg,
                'customer': customer.to_dict(),
            })

        messages.success(request, msg)
        return redirect('customers_list')


class CustomerDeleteView(PermissionRequiredMixin, View):
    """
    Handles customer deletion requests with financial protection.
    If the customer has financial or historical records, soft-deletes/archives.
    If the customer has zero historical records, performs hard delete or archive.
    """
    permission_required = 'customers.delete'

    def post(self, request, pk):
        customer = get_object_or_404(Customer, pk=pk)
        has_history = customer.has_historical_records()

        if has_history:
            customer.archive()
            msg = f"Client '{customer.company_name}' has historical records and was safely archived."
            action = 'archived'
        else:
            company_name = customer.company_name
            customer.delete()
            msg = f"Client '{company_name}' had no historical records and was permanently deleted."
            action = 'deleted'

        if is_ajax_request(request):
            return JsonResponse({
                'success': True,
                'action': action,
                'has_historical_records': has_history,
                'message': msg,
            })

        messages.success(request, msg)
        return redirect('customers_list')


class CustomerAPIView(PermissionRequiredMixin, View):
    """Authenticated CRUD API with financial protection."""
    permission_required = 'customers.view'

    def post(self, request):
        from apps.accounts.permissions import has_permission
        if not has_permission(request.user, 'customers.create'):
            return JsonResponse({'error': 'Forbidden'}, status=403)
        try:
            data = json.loads(request.body.decode('utf-8'))
        except Exception:
            return JsonResponse({'error': 'Invalid JSON'}, status=400)

        email = str(data.get('email', '')).strip()
        company_name = str(data.get('company_name', '')).strip()
        if not company_name or not email:
            return JsonResponse({'error': 'Company name and email are required.'}, status=400)
        try:
            validate_email(email)
        except ValidationError:
            return JsonResponse({'error': 'Invalid email address.'}, status=400)

        if Customer.objects.filter(email__iexact=email, is_active=True).exists():
            return JsonResponse({'error': 'An active client with this email already exists.'}, status=409)

        customer = Customer.objects.create(
            company_name=company_name,
            trading_name=str(data.get('trading_name', '')).strip(),
            contact_name=str(data.get('contact_name', '')).strip() or company_name,
            email=email,
            phone=str(data.get('phone', '')).strip(),
            vat_number=str(data.get('vat_number', '')).strip(),
            registration_number=str(data.get('registration_number', '')).strip(),
            physical_address=str(data.get('physical_address', '')).strip() or 'N/A',
            billing_address=str(data.get('billing_address', '')).strip(),
            payment_terms=str(data.get('payment_terms', Customer.PaymentTerms.DEPOSIT_50_POD_50)),
        )
        return JsonResponse({'success': True, 'customer': customer.to_dict()}, status=201)

    def get(self, request, pk=None):
        if pk:
            customer = get_object_or_404(Customer, pk=pk)
            return JsonResponse({'success': True, 'customer': customer.to_dict(), 'financial_summary': customer.get_financial_summary()})
        else:
            customers = Customer.objects.filter(is_active=True).order_by('company_name')
            return JsonResponse({'results': [c.to_dict() for c in customers], 'count': len(customers)})

    def patch(self, request, pk):
        from apps.accounts.permissions import has_permission
        if not has_permission(request.user, 'customers.update'):
            return JsonResponse({'error': 'Forbidden'}, status=403)
        try:
            data = json.loads(request.body.decode('utf-8'))
        except Exception:
            return JsonResponse({'error': 'Invalid JSON'}, status=400)

        customer = get_object_or_404(Customer, pk=pk)
        for field in ('company_name', 'trading_name', 'contact_name', 'email', 'phone', 'vat_number', 'registration_number', 'physical_address', 'billing_address', 'payment_terms'):
            if field in data:
                setattr(customer, field, str(data[field]).strip())

        if 'email' in data:
            try:
                validate_email(customer.email)
            except ValidationError:
                return JsonResponse({'error': 'Invalid email address'}, status=400)
            if Customer.objects.filter(email__iexact=customer.email, is_active=True).exclude(pk=pk).exists():
                return JsonResponse({'error': 'Another active client already uses this email.'}, status=409)

        customer.save()
        return JsonResponse({'success': True, 'customer': customer.to_dict()})

    def delete(self, request, pk):
        from apps.accounts.permissions import has_permission
        if not has_permission(request.user, 'customers.delete'):
            return JsonResponse({'error': 'Forbidden'}, status=403)

        customer = get_object_or_404(Customer, pk=pk)
        has_history = customer.has_historical_records()
        if has_history:
            customer.archive()
            return JsonResponse({'success': True, 'action': 'archived', 'message': 'Client was archived to protect historical records.'})
        else:
            customer.delete()
            return JsonResponse({'success': True, 'action': 'deleted', 'message': 'Client was permanently deleted.'})


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
                models.Q(trading_name__icontains=query) |
                models.Q(vat_number__icontains=query)
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

        company_name = str(data.get('company_name', '')).strip()
        contact_name = str(data.get('contact_name', '')).strip() or company_name
        email = str(data.get('email', '')).strip()
        phone = str(data.get('phone', '')).strip()
        vat_number = str(data.get('vat_number', '')).strip()
        physical_address = str(data.get('physical_address', '')).strip() or 'N/A'
        billing_address = str(data.get('billing_address', '')).strip()
        payment_terms = str(data.get('payment_terms', Customer.PaymentTerms.DEPOSIT_50_POD_50)).strip()

        if not company_name:
            return JsonResponse({'success': False, 'error': 'Company name is required.'}, status=400)
        if not email:
            return JsonResponse({'success': False, 'error': 'Email address is required.'}, status=400)

        try:
            validate_email(email)
        except ValidationError:
            return JsonResponse({'success': False, 'error': 'Please enter a valid email address.'}, status=400)

        customer = Customer.objects.filter(email__iexact=email).first()
        created = False

        if customer:
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
            customer.is_active = True
            customer.save()
        else:
            customer = Customer.objects.create(
                company_name=company_name,
                contact_name=contact_name,
                email=email,
                phone=phone,
                vat_number=vat_number,
                physical_address=physical_address,
                billing_address=billing_address,
                payment_terms=payment_terms,
                is_active=True,
            )
            created = True

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
        if request.content_type == 'application/json':
            try:
                data = json.loads(request.body.decode('utf-8'))
            except Exception:
                data = {}
        else:
            data = request.POST

        name = str(data.get('name', '')).strip()
        description = str(data.get('description', '')).strip()

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
        terms = [
            {'code': t.code, 'name': t.name}
            for t in PaymentTermOption.get_all_terms()
        ]
        return JsonResponse({'terms': terms, 'count': len(terms)})



