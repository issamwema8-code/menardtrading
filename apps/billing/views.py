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
                notes="100% Tax Invoice for transport consignment."
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

        # Update Invoice balance & status
        inv.amount_paid += amount_paid
        inv.balance_due = max(inv.total_amount - inv.amount_paid, Decimal('0.00'))
        if inv.balance_due == Decimal('0.00'):
            inv.status = Invoice.Status.PAID
        else:
            inv.status = Invoice.Status.PARTIALLY_PAID
        inv.save()

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

        messages.success(request, f"Payment of R{amount_paid} recorded. Receipt #{receipt.receipt_number} sent to client.")
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
