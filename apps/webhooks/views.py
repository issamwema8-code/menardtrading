import json
import base64
import uuid
import logging
from django.utils import timezone
from django.core.files.base import ContentFile
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.views import View
from apps.orders.models import PurchaseOrder
from apps.orders.services import process_inbound_purchase_order
from menard_core.brevo_email import send_departmental_email

logger = logging.getLogger(__name__)


@method_decorator(csrf_exempt, name='dispatch')
class BrevoInboundWebhookView(View):
    """
    Receives inbound email payloads from Brevo webhook dispatcher.
    Extracts PO email content, attachments, and triggers the automated order parser.
    Sends an automated no-reply email on receipt success or processing failure.
    """

    def post(self, request, *args, **kwargs):
        try:
            payload = {}
            if request.content_type == 'application/json':
                payload = json.loads(request.body.decode('utf-8'))
            else:
                # Fallback for form-encoded payloads
                payload = request.POST.dict()

            logger.info(f"Received Brevo Inbound Webhook: {list(payload.keys())}")

            items = payload.get('items', [])
            if not items and 'Sender' in payload:
                items = [payload]

            processed_orders = []

            for item in items:
                sender_info = item.get('Sender', {})
                sender_email = (sender_info.get('Email') if isinstance(sender_info, dict) else sender_info) or ''
                subject = item.get('Subject', 'Logistics PO Inbound')
                body_text = item.get('ExtractedMarkdownMessage') or item.get('RawHtmlBody') or item.get('Message', '')
                
                # Generate temporary unique PO reference if not parsed yet
                temp_po_ref = f"PO-INB-{uuid.uuid4().hex[:8].upper()}"

                try:
                    po = PurchaseOrder.objects.create(
                        po_number=temp_po_ref,
                        raw_email_sender=sender_email,
                        raw_email_subject=subject,
                        raw_email_body=body_text,
                        status=PurchaseOrder.Status.RECEIVED
                    )

                    # Process attachments
                    attachments = item.get('Attachments', [])
                    for att in attachments:
                        att_name = att.get('Name', 'purchase_order.pdf')
                        att_content = att.get('Content', '')
                        if att_content:
                            try:
                                file_data = base64.b64decode(att_content)
                                po.po_file.save(att_name, ContentFile(file_data), save=True)
                                break  # Use the first valid PDF attachment
                            except Exception as att_err:
                                logger.error(f"Failed to decode attachment {att_name}: {att_err}")

                    # Trigger parsing and draft quotation creation
                    process_inbound_purchase_order(po)
                    processed_orders.append(po.po_number)

                except Exception as item_err:
                    logger.error(f"Error processing PO item from {sender_email}: {item_err}", exc_info=True)
                    
                    # Dispatch notification notice to customer (strictly once)
                    if sender_email:
                        already_sent = po.acknowledgment_sent if ('po' in locals() and po) else False
                        if not already_sent:
                            try:
                                success, _ = send_departmental_email(
                                    department='no-reply',
                                    recipient_list=[sender_email],
                                    subject=f"Regarding your Purchase Order | Menard Trading CC",
                                    template_name='emails/po_failed_noreply.html',
                                    context={
                                        'subject': subject,
                                        'error_message': "We were unable to read the attached purchase order document. Please reply with a clear PDF copy."
                                    }
                                )
                                if success and 'po' in locals() and po and po.pk:
                                    po.acknowledgment_sent = True
                                    po.acknowledgment_sent_at = timezone.now()
                                    po.save(update_fields=['acknowledgment_sent', 'acknowledgment_sent_at'])
                            except Exception as email_err:
                                logger.error(f"Failed to dispatch error notice to {sender_email}: {email_err}")

            return JsonResponse({
                'status': 'success',
                'message': f'Processed {len(processed_orders)} inbound orders',
                'orders': processed_orders
            }, status=200)

        except Exception as e:
            logger.error(f"Brevo webhook critical error: {str(e)}", exc_info=True)
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

    def get(self, request, *args, **kwargs):
        return JsonResponse({
            'status': 'active',
            'service': 'Menard Trading CC Brevo Inbound Webhook Listener',
            'version': '1.0'
        })
