from django.shortcuts import render, get_object_or_404, redirect
from django.views import View
from django.utils import timezone
from django.contrib import messages
from apps.accounts.permissions import PermissionRequiredMixin
from apps.logistics.models import LogisticsJob
from apps.billing.models import Invoice
from menard_core.brevo_email import send_departmental_email


class UpdateJobStatusView(PermissionRequiredMixin, View):
    """
    Updates freight status (DISPATCHED, IN_TRANSIT, DELIVERED, POD_RECEIVED).
    """
    permission_required = 'logistics.update'

    def post(self, request, pk):
        job = get_object_or_404(LogisticsJob, pk=pk)
        new_status = request.POST.get('status')
        
        if new_status in LogisticsJob.Status.values:
            job.status = new_status
            if new_status == LogisticsJob.Status.DISPATCHED and not job.dispatch_date:
                job.dispatch_date = timezone.now()
            elif new_status == LogisticsJob.Status.DELIVERED and not job.delivery_date:
                job.delivery_date = timezone.now()
            
            # Fleet assignment updates if provided
            if request.POST.get('vehicle_registration'):
                job.vehicle_registration = request.POST.get('vehicle_registration')
            if request.POST.get('driver_name'):
                job.driver_name = request.POST.get('driver_name')
            if request.POST.get('driver_phone'):
                job.driver_phone = request.POST.get('driver_phone')

            job.save()
            messages.success(request, f"Job #{job.job_number} status updated to {job.get_status_display()}.")

        return redirect(request.META.get('HTTP_REFERER', 'dashboard_index'))


class UploadPODView(PermissionRequiredMixin, View):
    """
    Uploads Proof of Delivery (POD) document and updates status to POD_RECEIVED.
    """
    permission_required = 'logistics.upload_pod'
    def post(self, request, pk):
        job = get_object_or_404(LogisticsJob, pk=pk)
        pod_file = request.FILES.get('pod_document')

        if pod_file:
            job.pod_document = pod_file
            job.pod_uploaded_at = timezone.now()
            job.status = LogisticsJob.Status.POD_RECEIVED
            job.save()

            # Send POD confirmation email to customer
            if job.customer and job.customer.email:
                send_departmental_email(
                    department='operations',
                    recipient_list=[job.customer.email],
                    subject=f"Proof of Delivery – Job #{job.job_number} | Menard Trading CC",
                    template_name='emails/pod_received.html',
                    context={'job': job}
                )

            messages.success(request, f"Signed POD uploaded for Job #{job.job_number}.")
        else:
            messages.error(request, "No POD file selected.")

        return redirect(request.META.get('HTTP_REFERER', 'dashboard_index'))
