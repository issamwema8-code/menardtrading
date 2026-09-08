from django.contrib import admin
from .models import LogisticsJob


@admin.register(LogisticsJob)
class LogisticsJobAdmin(admin.ModelAdmin):
    list_display = ['job_number', 'customer', 'status', 'vehicle_registration', 'driver_name', 'driver_phone', 'scheduled_date', 'created_at']
    list_filter = ['status', 'scheduled_date', 'created_at']
    search_fields = ['job_number', 'customer__company_name', 'vehicle_registration', 'driver_name', 'pickup_address', 'delivery_address']
