import logging
from decimal import Decimal
from django.conf import settings
from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse, Http404
from django.utils import timezone
from django.views import View
from django.contrib import messages
from apps.accounts.permissions import PermissionRequiredMixin
from apps.accounts.audit import log_audit_event
from apps.billing.models import Invoice, InvoiceLineItem, PaymentReceipt
from apps.logistics.models import LogisticsJob
from apps.quotes.models import Quotation
from apps.billing.pdf_services import generate_invoice_pdf, generate_receipt_pdf, generate_quotation_pdf
from menard_core.brevo_email import send_departmental_email

logger = logging.getLogger(__name__)


class IssueInvoiceActionView(PermissionRequiredMixin, View):
    """
    Handles generation of all multi-stage invoices (Full, Deposit, Balance on POD, and Add-on Charges).
    """
    permission_required = 'invoices.create'
    def post(self, request, job_id):
        job = get_object_or_404(LogisticsJob, pk=job_id)
        invoice_type = request.POST.get('invoice_type', Invoice.InvoiceType.FULL)
        due_days = int(request.POST.get('due_days', 7))
        due_date = timezone.now().date() + timezone.timedelta(days=due_days)

        if invoice_type == Invoice.InvoiceType.FULL:
            inv = Invoice.objects.create(
                job=job,
                quote=job.quote,
                customer=job.customer,
                invoice_type=Invoice.InvoiceType.FULL,
                due_date=due_date,
                notes="Standard Full Invoice for transport consignment."
            )
            for item in job.quote.line_items.all():
                InvoiceLineItem.objects.create(
                    invoice=inv,
                    description=item.description,
                    quantity=item.quantity,
                    unit_price=item.unit_price,
                )
            inv.recalculate_totals()

        elif invoice_type == Invoice.InvoiceType.PARTIAL_DEPOSIT:
            pct = Decimal(request.POST.get('deposit_percentage', '50'))
            deposit_amount = (job.quote.subtotal * (pct / Decimal('100.00'))).quantize(Decimal('0.01'))
            inv = Invoice.objects.create(
                job=job,
                quote=job.quote,
                customer=job.customer,
                invoice_type=Invoice.InvoiceType.PARTIAL_DEPOSIT,
                due_date=due_date,
                notes=f"{pct}% Mobilization deposit."
            )
            InvoiceLineItem.objects.create(
                invoice=inv,
                description=f"{pct}% Deposit for Freight Consignment #{job.job_number}",
                quantity=1,
                unit_price=deposit_amount,
            )
            inv.recalculate_totals()

        elif invoice_type == Invoice.InvoiceType.PARTIAL_BALANCE:
            # Remaining uninvoiced balance
            remaining_subtotal = max(Decimal('0.00'), job.quote.subtotal - sum(
                (i.subtotal for i in job.invoices.filter(invoice_type=Invoice.InvoiceType.PARTIAL_DEPOSIT)),
                Decimal('0.00')
            ))
            inv = Invoice.objects.create(
                job=job,
                quote=job.quote,
                customer=job.customer,
                invoice_type=Invoice.InvoiceType.PARTIAL_BALANCE,
                due_date=due_date,
                notes=f"Final balance invoice upon Proof of Delivery for Job #{job.job_number}."
            )
            InvoiceLineItem.objects.create(
                invoice=inv,
                description=f"Final Balance on Delivery - Job #{job.job_number}",
                quantity=1,
                unit_price=remaining_subtotal,
            )
            inv.recalculate_totals()

        elif invoice_type == Invoice.InvoiceType.ADD_ON:
            description = request.POST.get('addon_description', 'Demurrage / Route Waiting Time')
            amount = Decimal(request.POST.get('addon_amount', '450.00'))
            inv = Invoice.objects.create(
                job=job,
                quote=job.quote,
                customer=job.customer,
                invoice_type=Invoice.InvoiceType.ADD_ON,
                due_date=due_date,
                notes="Supplementary charge for route additions / waiting time."
            )
            InvoiceLineItem.objects.create(
                invoice=inv,
                description=description,
                quantity=1,
                unit_price=amount,
            )
            inv.recalculate_totals()

        # Generate PDF and Email Customer
        generate_invoice_pdf(inv)
        if request.POST.get('send_email', 'true') == 'true' and inv.customer.email:
            send_departmental_email(
                department='invoicing',
                recipient_list=[inv.customer.email],
                subject=f"Tax Invoice #{inv.invoice_number} – Menard Trading CC",
                template_name='emails/invoice_sent.html',
                context={'invoice': inv},
                attachments=[(f"{inv.invoice_number}.pdf", inv.invoice_pdf.read(), 'application/pdf')] if inv.invoice_pdf else None
            )

        messages.success(request, f"Invoice #{inv.invoice_number} ({inv.get_invoice_type_display()}) generated & emailed.")
        return redirect(request.META.get('HTTP_REFERER', 'billing_list'))


class RecordPaymentActionView(PermissionRequiredMixin, View):
    """
    Records payment against an Invoice, creates receipt, and dispatches official PDF receipt to customer.
    """
    permission_required = 'invoices.record_payment'

    def post(self, request, invoice_id):
        inv = get_object_or_404(Invoice, pk=invoice_id)
        amount_paid = Decimal(request.POST.get('amount_paid', str(inv.balance_due)))
        method = request.POST.get('payment_method', PaymentReceipt.PaymentMethod.EFT)
        tx_ref = request.POST.get('transaction_reference', f"EFT-{timezone.now().strftime('%Y%m%d%H%M')}")
        notes = request.POST.get('notes', '')

        receipt = PaymentReceipt.objects.create(
            invoice=inv,
            customer=inv.customer,
            amount_paid=amount_paid,
            payment_method=method,
            transaction_reference=tx_ref,
            payment_date=timezone.now().date(),
            notes=notes
        )

        # Refresh invoice and receipt from DB (PaymentReceipt.save automatically recalculated invoice totals)
        inv.refresh_from_db()
        receipt.refresh_from_db()

        # Update associated Job if fully settled
        job = inv.job
        if job and job.remaining_uninvoiced == Decimal('0.00') and all(i.status == Invoice.Status.PAID for i in job.invoices.all()):
            job.status = LogisticsJob.Status.CLOSED
            job.save(update_fields=['status'])

        # Generate Receipt PDF
        pdf_bytes = generate_receipt_pdf(receipt)

        # Log financial audit event
        log_audit_event(
            request=request,
            user=request.user,
            action='PAYMENT_RECORDED',
            resource_type='Invoice',
            resource_id=inv.invoice_number,
            details={'amount': str(amount_paid), 'method': method, 'ref': tx_ref, 'receipt_number': receipt.receipt_number},
            result='SUCCESS'
        )

        # Automatically email Receipt to customer via Brevo
        if inv.customer.email:
            send_departmental_email(
                department='invoicing',
                recipient_list=[inv.customer.email],
                subject=f"Payment Receipt #{receipt.receipt_number} – Menard Trading CC",
                template_name='emails/receipt_sent.html',
                context={'receipt': receipt},
                attachments=[(f"{receipt.receipt_number}.pdf", pdf_bytes, 'application/pdf')] if pdf_bytes else None
            )

        from menard_core.formatters import format_money
        messages.success(request, f"Payment of {format_money(amount_paid, 'R')} recorded. Receipt #{receipt.receipt_number} sent to client.")
        return redirect(request.META.get('HTTP_REFERER', 'receipts_list'))


class InvoicePreviewView(PermissionRequiredMixin, View):
    permission_required = 'invoices.view'

    def get(self, request, pk):
        inv = get_object_or_404(Invoice, pk=pk)
        return render(request, 'documents/invoice_preview.html', {
            'invoice': inv,
            'active_tab': 'billing'
        })


class ReceiptPreviewView(PermissionRequiredMixin, View):
    permission_required = 'receipts.view'

    def get(self, request, pk):
        rcp = get_object_or_404(PaymentReceipt, pk=pk)
        return render(request, 'documents/receipt_preview.html', {
            'receipt': rcp,
            'active_tab': 'receipts'
        })



class InvoicePDFDownloadView(PermissionRequiredMixin, View):
    permission_required = 'invoices.download'

    def get(self, request, pk):
        inv = get_object_or_404(Invoice, pk=pk)
        pdf_bytes = generate_invoice_pdf(inv)
        response = HttpResponse(pdf_bytes or (inv.invoice_pdf.read() if inv.invoice_pdf else b""), content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="{inv.invoice_number}.pdf"'
        return response


class ReceiptPDFDownloadView(PermissionRequiredMixin, View):
    permission_required = 'receipts.download'

    def get(self, request, pk):
        rcp = get_object_or_404(PaymentReceipt, pk=pk)
        pdf_bytes = generate_receipt_pdf(rcp)
        response = HttpResponse(pdf_bytes or (rcp.receipt_pdf.read() if rcp.receipt_pdf else b""), content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="{rcp.receipt_number}.pdf"'
        return response


class QuotationPDFDownloadView(PermissionRequiredMixin, View):
    permission_required = 'quotations.download'

    def get(self, request, pk):
        quote = get_object_or_404(Quotation, pk=pk)
        pdf_bytes = generate_quotation_pdf(quote)
        response = HttpResponse(pdf_bytes or (quote.quote_pdf.read() if quote.quote_pdf else b""), content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="{quote.quote_number}.pdf"'
        return response


class CreateDirectInvoiceView(PermissionRequiredMixin, View):
    """
    Directly create a new Tax Invoice without requiring a pre-existing Quotation or Logistics Job.
    Supports dedicated full-page UI and multi-row line items with descriptions, quantities, and unit prices.
    """
    permission_required = 'invoices.create'

    def get(self, request):
        from apps.customers.models import Customer
        from apps.accounts.models import CompanySettings

        customers = Customer.objects.filter(is_active=True).order_by('company_name')
        comp_settings = CompanySettings.get_settings()
        selected_customer_id = request.GET.get('customer_id', '')

        return render(request, 'billing/invoice_create.html', {
            'customers': customers,
            'selected_customer_id': selected_customer_id,
            'default_vat_rate': comp_settings.vat_rate,
            'active_tab': 'billing',
        })

    def post(self, request):
        from apps.customers.models import Customer
        from apps.accounts.models import CompanySettings

        customer_id = request.POST.get('customer_id') or request.POST.get('customer')
        customer = get_object_or_404(Customer, pk=customer_id)

        invoice_type = request.POST.get('invoice_type', Invoice.InvoiceType.FULL)
        if invoice_type not in Invoice.InvoiceType.values:
            invoice_type = Invoice.InvoiceType.FULL

        due_days = int(request.POST.get('due_days', 30))
        due_date = timezone.now().date() + timezone.timedelta(days=due_days)
        notes = request.POST.get('notes', 'Payment strictly according to agreed terms. Direct EFT into Menard Trading CC bank account.')

        # Get active company VAT rate or form input
        vat_rate_post = request.POST.get('vat_rate')
        if vat_rate_post is not None and str(vat_rate_post).strip() != '':
            try:
                settings_vat = Decimal(str(vat_rate_post).strip())
            except Exception:
                settings_vat = CompanySettings.get_settings().vat_rate
        else:
            settings_vat = CompanySettings.get_settings().vat_rate

        inv = Invoice.objects.create(
            customer=customer,
            invoice_type=invoice_type,
            due_date=due_date,
            vat_rate=settings_vat,
            notes=notes,
            status=Invoice.Status.ISSUED
        )

        descriptions = request.POST.getlist('descriptions[]') or request.POST.getlist('description')
        quantities = request.POST.getlist('quantities[]') or request.POST.getlist('quantity')
        unit_prices = request.POST.getlist('unit_prices[]') or request.POST.getlist('unit_price')

        items_created = 0
        if descriptions:
            for idx, desc in enumerate(descriptions):
                desc_str = str(desc).strip()
                if not desc_str:
                    continue
                qty_raw = quantities[idx] if idx < len(quantities) else '1.00'
                price_raw = unit_prices[idx] if idx < len(unit_prices) else '0.00'
                try:
                    qty = Decimal(str(qty_raw).strip() or '1.00')
                except Exception:
                    qty = Decimal('1.00')
                try:
                    price = Decimal(str(price_raw).strip() or '0.00')
                except Exception:
                    price = Decimal('0.00')

                InvoiceLineItem.objects.create(
                    invoice=inv,
                    description=desc_str,
                    quantity=qty,
                    unit_price=price
                )
                items_created += 1

        if items_created == 0:
            InvoiceLineItem.objects.create(
                invoice=inv,
                description=request.POST.get('description', 'Direct Supply & Logistics Service') or 'Direct Supply & Logistics Service',
                quantity=Decimal(request.POST.get('quantity', '1.00') or '1.00'),
                unit_price=Decimal(request.POST.get('unit_price', '0.00') or '0.00')
            )

        inv.recalculate_totals()
        pdf_bytes = generate_invoice_pdf(inv)

        log_audit_event(
            request=request,
            user=request.user,
            action='DIRECT_INVOICE_CREATED',
            resource_type='Invoice',
            resource_id=inv.invoice_number,
            details={'customer': customer.company_name, 'total_amount': str(inv.total_amount), 'items': items_created},
            result='SUCCESS'
        )

        # Send email to customer if requested
        if request.POST.get('send_email') == '1' and inv.customer.email:
            send_departmental_email(
                department='invoicing',
                recipient_list=[inv.customer.email],
                subject=f"Tax Invoice #{inv.invoice_number} – Menard Trading CC",
                template_name='emails/invoice_sent.html',
                context={'invoice': inv},
                attachments=[(f"{inv.invoice_number}.pdf", pdf_bytes or inv.invoice_pdf.read(), 'application/pdf')] if (pdf_bytes or inv.invoice_pdf) else None
            )

        from menard_core.formatters import format_money
        messages.success(request, f"Created Tax Invoice #{inv.invoice_number} for {customer.company_name} (Total: {format_money(inv.total_amount, 'N$')}).")
        return redirect('invoice_preview', pk=inv.id)

