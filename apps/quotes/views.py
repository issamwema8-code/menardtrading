import logging
from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views import View
from django.contrib import messages
from apps.accounts.permissions import PermissionRequiredMixin
from apps.quotes.models import Quotation
from apps.logistics.models import LogisticsJob
from apps.billing.models import Invoice, InvoiceLineItem
from apps.billing.pdf_services import generate_quotation_pdf, generate_invoice_pdf
from menard_core.brevo_email import send_departmental_email

logger = logging.getLogger(__name__)


class CustomerQuotePortalView(View):
    """
    Public customer-facing portal to view and review a quotation via a secure token.
    """
    def get(self, request, token):
        quote = get_object_or_404(Quotation, approval_token=token)
        return render(request, 'portal/quote_view.html', {
            'quote': quote,
            'active_tab': 'quotes'
        })



class CustomerApproveQuoteView(View):
    """
    1-Click Quote Approval by the Customer.
    Automatically creates a LogisticsJob and initiates the first-stage invoice.
    """
    def post(self, request, token):
        quote = get_object_or_404(Quotation, approval_token=token)
        
        if quote.status == Quotation.Status.APPROVED:
            messages.info(request, "This quotation has already been approved.")
            return redirect('quote_portal', token=token)

        quote.status = Quotation.Status.APPROVED
        quote.approved_at = timezone.now()
        quote.save(update_fields=['status', 'approved_at'])

        # 1. Automatically create Logistics Job
        po = quote.purchase_order
        job = LogisticsJob.objects.create(
            quote=quote,
            customer=quote.customer,
            status=LogisticsJob.Status.BOOKED,
            pickup_address=po.pickup_location if po else quote.customer.physical_address,
            delivery_address=po.delivery_location if po else 'As specified',
            cargo_summary=po.cargo_description if po else 'General Freight',
            weight_tons=po.weight_tons if po else None,
            scheduled_date=timezone.now().date(),
            notes=f"Auto-generated from approved Quote #{quote.quote_number} (PO #{po.po_number if po else 'N/A'})"
        )

        # 2. Automatically generate first invoice according to Customer Payment Terms
        terms = quote.customer.payment_terms
        due_date = timezone.now().date() + timezone.timedelta(days=7)

        if terms == '100_UPFRONT':
            inv = Invoice.objects.create(
                job=job,
                quote=quote,
                customer=quote.customer,
                invoice_type=Invoice.InvoiceType.FULL,
                due_date=due_date,
                notes="100% Upfront payment before truck dispatch."
            )
            for item in quote.line_items.all():
                InvoiceLineItem.objects.create(
                    invoice=inv,
                    description=item.description,
                    quantity=item.quantity,
                    unit_price=item.unit_price,
                )
            inv.recalculate_totals()
            generate_invoice_pdf(inv)

        elif terms in ['50_DEPOSIT_50_POD', '30_DEPOSIT_70_POD']:
            pct = 50 if terms == '50_DEPOSIT_50_POD' else 30
            inv = Invoice.objects.create(
                job=job,
                quote=quote,
                customer=quote.customer,
                invoice_type=Invoice.InvoiceType.PARTIAL_DEPOSIT,
                due_date=due_date,
                notes=f"{pct}% Mobilization deposit required prior to loading."
            )
            deposit_subtotal = (quote.subtotal * (pct / 100))
            InvoiceLineItem.objects.create(
                invoice=inv,
                description=f"{pct}% Mobilization Deposit for Job #{job.job_number} ({quote.quote_number})",
                quantity=1,
                unit_price=deposit_subtotal,
            )
            inv.recalculate_totals()
            generate_invoice_pdf(inv)

        # Send notification email with the new invoice to the customer
        if 'inv' in locals() and quote.customer.email:
            send_departmental_email(
                department='invoicing',
                recipient_list=[quote.customer.email],
                subject=f"Tax Invoice #{inv.invoice_number} – Menard Trading CC",
                template_name='emails/invoice_sent.html',
                context={'invoice': inv},
                attachments=[(f"{inv.invoice_number}.pdf", inv.invoice_pdf.read(), 'application/pdf')] if inv.invoice_pdf else None
            )

        messages.success(request, f"Quotation #{quote.quote_number} approved! Job #{job.job_number} created.")
        return redirect('quote_portal', token=token)


class SendQuoteEmailView(PermissionRequiredMixin, View):
    """
    Staff action: Sends quotation PDF with interactive 1-click approval link to customer via Brevo.
    """
    permission_required = 'quotations.send'

    def post(self, request, pk):
        quote = get_object_or_404(Quotation, pk=pk)
        
        # Ensure totals and PDF exist
        quote.recalculate_totals()
        pdf_bytes = generate_quotation_pdf(quote)

        approval_url = request.build_absolute_uri(f"/portal/quotes/{quote.approval_token}/")

        success, msg = send_departmental_email(
            department='quotes',
            recipient_list=[quote.customer.email],
            subject=f"Quotation #{quote.quote_number} – Menard Trading CC",
            template_name='emails/quote_sent.html',
            context={
                'quote': quote,
                'approval_url': approval_url
            },
            attachments=[(f"{quote.quote_number}.pdf", pdf_bytes, 'application/pdf')] if pdf_bytes else None
        )

        if success:
            quote.status = Quotation.Status.SENT
            quote.sent_at = timezone.now()
            quote.save(update_fields=['status', 'sent_at'])
            messages.success(request, f"Quotation #{quote.quote_number} emailed to {quote.customer.email}.")
        else:
            messages.error(request, f"Failed to send email: {msg}")

        return redirect(request.META.get('HTTP_REFERER', 'quotes_list'))


class CreateQuotationView(PermissionRequiredMixin, View):
    """
    Manually create a real quotation for a customer with custom line items.
    """
    permission_required = 'quotations.create'
    def post(self, request):
        from apps.customers.models import Customer
        from apps.quotes.models import QuoteLineItem
        from decimal import Decimal

        customer_id = request.POST.get('customer_id')
        customer = get_object_or_404(Customer, pk=customer_id)
        
        notes = request.POST.get('notes', 'Standard freight transport terms apply.')
        valid_days = int(request.POST.get('valid_days', 14))

        quote = Quotation.objects.create(
            customer=customer,
            valid_until=timezone.now().date() + timezone.timedelta(days=valid_days),
            notes=notes,
            status=Quotation.Status.DRAFT
        )

        desc = request.POST.get('description', 'Freight Service')
        qty = Decimal(request.POST.get('quantity', '1.00'))
        price = Decimal(request.POST.get('unit_price', '0.00'))
        item_type = request.POST.get('item_type', QuoteLineItem.ItemType.FREIGHT)

        QuoteLineItem.objects.create(
            quote=quote,
            description=desc,
            quantity=qty,
            unit_price=price,
            item_type=item_type
        )
        quote.recalculate_totals()
        generate_quotation_pdf(quote)

        messages.success(request, f"Created Quotation #{quote.quote_number} for {customer.company_name} (Total: R{quote.total_amount}).")
        return redirect('quotes_list')


class QuotationPreviewView(PermissionRequiredMixin, View):
    permission_required = 'quotations.view'

    def get(self, request, pk):
        quote = get_object_or_404(Quotation, pk=pk)
        return render(request, 'documents/quotation_preview.html', {
            'quote': quote,
            'active_tab': 'quotes'
        })

