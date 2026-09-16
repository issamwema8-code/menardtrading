import json
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Role, SystemPermission, UserProfile
from apps.billing.models import Invoice, PaymentReceipt
from apps.customers.models import Customer, PaymentTermOption
from apps.logistics.models import LogisticsJob
from apps.orders.models import PurchaseOrder
from apps.quotes.models import Quotation


class CustomerCRUDTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('client-admin', 'admin@example.com', 'password')
        self.client.force_login(self.user)

    def payload(self, company='CoSMOS', email='issamwema8@gmail.com'):
        return {
            'company_name': company,
            'trading_name': 'CoSMOS Transport',
            'contact_name': 'issa mwema',
            'email': email,
            'phone': '+254 700 000 000',
            'payment_terms': Customer.PaymentTerms.DEPOSIT_50_POD_50,
            'vat_number': 'VAT-001',
            'registration_number': 'REG-2026-001',
            'physical_address': 'Ruiru 00232',
            'billing_address': 'PO Box 123, Ruiru 00232',
            'credit_limit': '50000.00',
        }

    def test_create_view_persists_and_prevents_active_duplicate_email(self):
        # 1. HTML Form Create
        response = self.client.post(reverse('create_customer'), self.payload())
        self.assertRedirects(response, reverse('customers_list'))
        customer = Customer.objects.get(email='issamwema8@gmail.com')
        self.assertEqual(customer.company_name, 'CoSMOS')
        self.assertEqual(customer.contact_name, 'issa mwema')
        self.assertTrue(customer.is_active)

        # 2. Prevent duplicate active client
        duplicate = self.client.post(reverse('create_customer'), self.payload(company='Duplicate'))
        self.assertRedirects(duplicate, reverse('customers_list'))
        self.assertEqual(Customer.objects.filter(email__iexact='issamwema8@gmail.com').count(), 1)

    def test_create_view_json_ajax_support(self):
        payload = self.payload(company='JSON Client', email='json@example.com')
        response = self.client.post(
            reverse('create_customer'),
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['customer']['company_name'], 'JSON Client')
        self.assertEqual(data['customer']['email'], 'json@example.com')

    def test_archived_client_email_reactivated_on_recreate(self):
        customer = Customer.objects.create(**self.payload(company='Old Corp', email='archived@example.com'))
        customer.archive()
        self.assertFalse(customer.is_active)

        # Creating again with same email reactivates the record
        response = self.client.post(reverse('create_customer'), self.payload(company='Reactivated Corp', email='archived@example.com'))
        self.assertRedirects(response, reverse('customers_list'))
        customer.refresh_from_db()
        self.assertTrue(customer.is_active)
        self.assertEqual(customer.company_name, 'Reactivated Corp')

    def test_list_view_and_json_filtering(self):
        c1 = Customer.objects.create(**self.payload(company='Alpha Logistics', email='alpha@example.com'))
        c2 = Customer.objects.create(**self.payload(company='Beta Freight', email='beta@example.com'))
        c3 = Customer.objects.create(**self.payload(company='Archived Ltd', email='archived@example.com'))
        c3.archive()

        # HTML List view
        res = self.client.get(reverse('customers_list'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Alpha Logistics')
        self.assertContains(res, 'Beta Freight')
        self.assertNotContains(res, 'Archived Ltd')

        # JSON AJAX List with Query Filter
        json_res = self.client.get(f"{reverse('customers_list')}?q=Alpha&format=json", HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(json_res.status_code, 200)
        data = json_res.json()
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['customers'][0]['company_name'], 'Alpha Logistics')

        # Archived View
        archived_res = self.client.get(f"{reverse('customers_list')}?archived=1")
        self.assertEqual(archived_res.status_code, 200)
        self.assertContains(archived_res, 'Archived Ltd')

    def test_detail_view_html_and_json_payload_with_financial_metrics(self):
        customer = Customer.objects.create(**self.payload())
        quote = Quotation.objects.create(customer=customer, total_amount=Decimal('15000.00'), status=Quotation.Status.APPROVED)
        inv = Invoice.objects.create(customer=customer, total_amount=Decimal('15000.00'), amount_paid=Decimal('5000.00'), balance_due=Decimal('10000.00'), due_date='2026-10-01')
        rcp = PaymentReceipt.objects.create(customer=customer, invoice=inv, amount_paid=Decimal('5000.00'), payment_date='2026-09-15')
        job = LogisticsJob.objects.create(customer=customer, quote=quote, driver_name='John Mwangi', vehicle_registration='KBZ 123A')
        po = PurchaseOrder.objects.create(customer=customer, po_number='PO-9999')

        # 1. HTML Detail View
        res = self.client.get(reverse('customer_detail', args=[customer.pk]))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'CoSMOS')
        self.assertContains(res, 'PO-9999')

        # 2. JSON Detail View for interactive modal
        json_res = self.client.get(f"{reverse('customer_detail', args=[customer.pk])}?format=json", HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(json_res.status_code, 200)
        data = json_res.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['customer']['company_name'], 'CoSMOS')
        self.assertEqual(data['financial_summary']['total_invoiced'], 15000.0)
        self.assertEqual(data['financial_summary']['total_paid'], 5000.0)
        self.assertEqual(data['financial_summary']['total_balance_due'], 10000.0)
        self.assertEqual(len(data['quotations']), 1)
        self.assertEqual(len(data['invoices']), 1)
        self.assertEqual(len(data['receipts']), 1)
        self.assertEqual(len(data['jobs']), 1)
        self.assertEqual(len(data['purchase_orders']), 1)

    def test_edit_customer_and_email_uniqueness(self):
        c1 = Customer.objects.create(**self.payload(company='Customer One', email='c1@example.com'))
        c2 = Customer.objects.create(**self.payload(company='Customer Two', email='c2@example.com'))

        # Edit C1 details
        edit_payload = self.payload(company='Customer One Updated', email='c1_new@example.com')
        edit_res = self.client.post(
            reverse('edit_customer', args=[c1.pk]),
            data=json.dumps(edit_payload),
            content_type='application/json',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(edit_res.status_code, 200)
        c1.refresh_from_db()
        self.assertEqual(c1.company_name, 'Customer One Updated')
        self.assertEqual(c1.email, 'c1_new@example.com')

        # Attempt to steal C2 email -> should fail
        steal_payload = self.payload(company='Customer One', email='c2@example.com')
        steal_res = self.client.post(
            reverse('edit_customer', args=[c1.pk]),
            data=json.dumps(steal_payload),
            content_type='application/json',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(steal_res.status_code, 409)

    def test_financial_client_is_archived_on_delete(self):
        customer = Customer.objects.create(**self.payload())
        Invoice.objects.create(customer=customer, due_date='2026-10-01', total_amount=Decimal('100.00'))
        
        # Call delete endpoint
        response = self.client.post(reverse('delete_customer', args=[customer.pk]), HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['action'], 'archived')
        customer.refresh_from_db()
        self.assertFalse(customer.is_active)
        self.assertTrue(Invoice.objects.filter(customer=customer).exists())

    def test_empty_client_is_permanently_deleted(self):
        customer = Customer.objects.create(**self.payload(company='Empty Corp', email='empty@example.com'))
        cust_id = customer.id
        response = self.client.post(reverse('delete_customer', args=[cust_id]), HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['action'], 'deleted')
        self.assertFalse(Customer.objects.filter(id=cust_id).exists())

    def test_restore_archived_customer(self):
        customer = Customer.objects.create(**self.payload(company='To Restore', email='restore@example.com'))
        customer.archive()
        self.assertFalse(customer.is_active)

        response = self.client.post(reverse('restore_customer', args=[customer.pk]), HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['action'], 'restored')
        customer.refresh_from_db()
        self.assertTrue(customer.is_active)

    def test_payment_terms_change_applies_to_future_documents_only(self):
        customer = Customer.objects.create(**self.payload())
        old_quote = Quotation.objects.create(customer=customer)
        customer.payment_terms = Customer.PaymentTerms.UPFRONT_100
        customer.save(update_fields=['payment_terms', 'updated_at'])
        new_quote = Quotation.objects.create(customer=customer)
        self.assertEqual(old_quote.payment_terms_snapshot, '50% Deposit / 50% on POD')
        self.assertEqual(new_quote.payment_terms_snapshot, '100% Upfront')

    def test_rbac_permissions_enforce_security(self):
        # Create non-privileged user without customer permissions
        regular_user = User.objects.create_user('staff-user', 'staff@example.com', 'password')
        self.client.force_login(regular_user)

        # Attempt to create -> 403
        res = self.client.post(reverse('create_customer'), self.payload(company='Hacker Corp', email='hack@example.com'), HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(res.status_code, 403)

        # Attempt to view list without customers.view -> 403
        list_res = self.client.get(reverse('customers_list'), HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(list_res.status_code, 403)

    def test_cosmos_existing_client_data_structure(self):
        cosmos = Customer.objects.create(
            company_name='CoSMOS',
            contact_name='issa mwema',
            email='issamwema8@gmail.com',
            payment_terms='50_DEPOSIT_50_POD',
            physical_address='Ruiru 00232'
        )
        self.assertEqual(cosmos.company_name, 'CoSMOS')
        self.assertEqual(cosmos.contact_name, 'issa mwema')
        self.assertEqual(cosmos.get_payment_terms_display(), '50% Deposit / 50% on POD')
        self.assertEqual(cosmos.display_address, 'Ruiru 00232')

    def test_standalone_customer_pages_and_navigation(self):
        # 1. Customer Create page GET /customers/create/
        res = self.client.get(reverse('create_customer'))
        self.assertEqual(res.status_code, 200)
        self.assertTemplateUsed(res, 'customers/customer_form.html')
        self.assertContains(res, 'Add New Client')
        self.assertContains(res, 'Save Client')

        # 2. Customer List page GET /customers/ has direct link to /customers/create/
        list_res = self.client.get(reverse('customers_list'))
        self.assertEqual(list_res.status_code, 200)
        self.assertContains(list_res, reverse('create_customer'))
        self.assertContains(list_res, '+ Add Client')

        # 3. Create a customer to test Detail and Edit pages
        customer = Customer.objects.create(**self.payload(company='Voyager Logistics', email='voyager@example.com'))
        
        # 4. Customer Detail page GET /customers/<id>/
        detail_res = self.client.get(reverse('customer_detail', args=[customer.id]))
        self.assertEqual(detail_res.status_code, 200)
        self.assertTemplateUsed(detail_res, 'customers/customer_detail.html')
        self.assertContains(detail_res, 'Voyager Logistics')
        self.assertContains(detail_res, reverse('edit_customer', args=[customer.id]))
        self.assertContains(detail_res, reverse('delete_customer', args=[customer.id]))

        # 5. Customer Edit page GET /customers/<id>/edit/
        edit_res = self.client.get(reverse('edit_customer', args=[customer.id]))
        self.assertEqual(edit_res.status_code, 200)
        self.assertTemplateUsed(edit_res, 'customers/customer_form.html')
        self.assertContains(edit_res, 'Edit Client: Voyager Logistics')
        self.assertContains(edit_res, 'Save Changes')

