import io
import logging
from django.conf import settings
from django.core.files.base import ContentFile
from django.template.loader import render_to_string
from xhtml2pdf import pisa

logger = logging.getLogger(__name__)


def render_html_to_pdf_bytes(template_src, context_dict={}):
    """
    Renders a Django template to a PDF binary buffer using xhtml2pdf.
    """
    try:
        from apps.accounts.models import CompanySettings
        company_branding = CompanySettings.get_settings().as_branding_dict()
    except Exception:
        company_branding = getattr(settings, 'EMAIL_BRANDING', {})

    full_context = {
        'branding': company_branding,
        'company': company_branding,
        'base_url': getattr(settings, 'BASE_URL', 'https://menardtrading.com'),
        **context_dict
    }

    html_string = render_to_string(template_src, full_context)
    result_buffer = io.BytesIO()
    
    # Render PDF
    pisa_status = pisa.pisaDocument(
        src=io.BytesIO(html_string.encode("UTF-8")),
        dest=result_buffer,
        encoding="UTF-8"
    )

    if pisa_status.err:
        logger.error(f"Error rendering PDF template {template_src}: {pisa_status.err}")
        return None

    return result_buffer.getvalue()


def generate_quotation_pdf(quotation) -> bytes:
    """
    Generates and saves the PDF file for a Quotation.
    """
    pdf_bytes = render_html_to_pdf_bytes('pdfs/quotation_pdf.html', {'quote': quotation})
    if pdf_bytes:
        filename = f"{quotation.quote_number}.pdf"
        quotation.quote_pdf.save(filename, ContentFile(pdf_bytes), save=True)
    return pdf_bytes


def generate_invoice_pdf(invoice) -> bytes:
    """
    Generates and saves the PDF file for an Invoice.
    """
    pdf_bytes = render_html_to_pdf_bytes('pdfs/invoice_pdf.html', {'invoice': invoice})
    if pdf_bytes:
        filename = f"{invoice.invoice_number.replace('/', '_')}.pdf"
        invoice.invoice_pdf.save(filename, ContentFile(pdf_bytes), save=True)
    return pdf_bytes


def generate_receipt_pdf(receipt) -> bytes:
    """
    Generates and saves the PDF file for a Payment Receipt.
    """
    pdf_bytes = render_html_to_pdf_bytes('pdfs/receipt_pdf.html', {'receipt': receipt})
    if pdf_bytes:
        filename = f"{receipt.receipt_number}.pdf"
        receipt.receipt_pdf.save(filename, ContentFile(pdf_bytes), save=True)
    return pdf_bytes
