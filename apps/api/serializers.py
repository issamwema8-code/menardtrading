from rest_framework import serializers
from apps.customers.models import Customer
from apps.orders.models import PurchaseOrder
from apps.quotes.models import Quotation, QuoteLineItem
from apps.logistics.models import LogisticsJob
from apps.billing.models import Invoice, InvoiceLineItem, PaymentReceipt


class CustomerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = '__all__'


class PurchaseOrderSerializer(serializers.ModelSerializer):
    customer_name = serializers.ReadOnlyField(source='customer.company_name')

    class Meta:
        model = PurchaseOrder
        fields = '__all__'


class QuoteLineItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = QuoteLineItem
        fields = '__all__'


class QuotationSerializer(serializers.ModelSerializer):
    customer_name = serializers.ReadOnlyField(source='customer.company_name')
    line_items = QuoteLineItemSerializer(many=True, read_only=True)

    class Meta:
        model = Quotation
        fields = '__all__'


class LogisticsJobSerializer(serializers.ModelSerializer):
    customer_name = serializers.ReadOnlyField(source='customer.company_name')
    quote_total = serializers.ReadOnlyField()
    total_invoiced = serializers.ReadOnlyField()
    total_paid = serializers.ReadOnlyField()
    remaining_uninvoiced = serializers.ReadOnlyField()

    class Meta:
        model = LogisticsJob
        fields = '__all__'


class InvoiceLineItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoiceLineItem
        fields = '__all__'


class InvoiceSerializer(serializers.ModelSerializer):
    customer_name = serializers.ReadOnlyField(source='customer.company_name')
    line_items = InvoiceLineItemSerializer(many=True, read_only=True)

    class Meta:
        model = Invoice
        fields = '__all__'


class PaymentReceiptSerializer(serializers.ModelSerializer):
    customer_name = serializers.ReadOnlyField(source='customer.company_name')
    invoice_number = serializers.ReadOnlyField(source='invoice.invoice_number')

    class Meta:
        model = PaymentReceipt
        fields = '__all__'
