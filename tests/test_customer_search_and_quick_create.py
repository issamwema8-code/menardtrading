import json
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from apps.customers.models import Customer
from apps.quotes.models import Quotation, QuoteLineItem

User = get_user_model()


class CustomerSearchAndQuickCreateTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_superuser(
            username='admin_test',
            email='admin@menardtrading.com',
            password='TestPassword123!'
        )
        self.client.force_login(self.user)

        self.c1 = Customer.objects.create(
            company_name='Anglo American Logistics',
            contact_name='Sipho Ndlovu',
            email='sipho@anglo.com',
            phone='+27 11 900 1122',
            physical_address='Johannesburg',
            payment_terms=Customer.PaymentTerms.DEPOSIT_50_POD_50
        )
        self.c2 = Customer.objects.create(
            company_name='Sasol Mining SA',
            contact_name='Kagiso Molefe',
            email='kagiso@sasol.com',
            phone='+27 11 900 3344',
            physical_address='Secunda',
            payment_terms=Customer.PaymentTerms.UPFRONT_100
        )
        self.c3 = Customer.objects.create(
            company_name='Client (PO-1)',
            contact_name='Katlego Ndimande',
            email='katnd77@gmail.com',
            phone='+27 82 123 4567',
            physical_address='Durban',
            payment_terms=Customer.PaymentTerms.DEPOSIT_50_POD_50
        )

    def test_customer_search_api_empty_query(self):
        url = reverse('customer_search_api')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('results', data)
        self.assertEqual(len(data['results']), 3)

    def test_customer_search_api_filtering(self):
        url = reverse('customer_search_api')
        
        # Search by company name
        response = self.client.get(url, {'q': 'Sasol'})
        data = response.json()
        self.assertEqual(len(data['results']), 1)
        self.assertEqual(data['results'][0]['company_name'], 'Sasol Mining SA')

        # Search by email
        response = self.client.get(url, {'q': 'katnd77@gmail.com'})
        data = response.json()
        self.assertEqual(len(data['results']), 1)
        self.assertEqual(data['results'][0]['email'], 'katnd77@gmail.com')

        # Search by contact name
        response = self.client.get(url, {'q': 'Sipho'})
        data = response.json()
        self.assertEqual(len(data['results']), 1)
        self.assertEqual(data['results'][0]['contact_name'], 'Sipho Ndlovu')

    def test_quick_create_customer_success(self):
        url = reverse('quick_create_customer')
        payload = {
            'company_name': 'Kudumane Manganese',
            'contact_name': 'Johan Van Der Merwe',
            'email': 'johan@kudumane.co.za',
            'phone': '+27 53 742 1000',
            'payment_terms': '50_DEPOSIT_50_POD',
            'physical_address': 'Hotazel, Northern Cape'
        }
        response = self.client.post(
            url,
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['customer']['company_name'], 'Kudumane Manganese')
        self.assertEqual(data['customer']['email'], 'johan@kudumane.co.za')

        # Verify in DB
        cust = Customer.objects.get(email='johan@kudumane.co.za')
        self.assertEqual(cust.company_name, 'Kudumane Manganese')
        self.assertEqual(cust.phone, '+27 53 742 1000')

    def test_quick_create_customer_validation_error(self):
        url = reverse('quick_create_customer')
        # Missing email
        payload = {
            'company_name': 'No Email Corp',
            'email': ''
        }
        response = self.client.post(
            url,
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertFalse(data['success'])
        self.assertIn('error', data)

    def test_create_quotation_with_newly_created_customer(self):
        # 1. Quick create customer
        quick_url = reverse('quick_create_customer')
        payload = {
            'company_name': 'BHP Billiton Freight',
            'contact_name': 'David Moyo',
            'email': 'david.moyo@bhp.com',
            'phone': '+27 11 376 9000',
            'payment_terms': '100_UPFRONT'
        }
        res = self.client.post(quick_url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(res.status_code, 200)
        cust_id = res.json()['customer']['id']

        # 2. Create quotation using customer_id
        quote_url = reverse('create_quotation')
        quote_data = {
            'customer_id': cust_id,
            'description': '34-Ton Long Haul Freight JHB to Cape Town',
            'quantity': '1.00',
            'unit_price': '25000.00',
            'valid_days': '14',
            'notes': 'Standard logistics quote'
        }
        res_quote = self.client.post(quote_url, data=quote_data)
        self.assertEqual(res_quote.status_code, 302)

        # 3. Verify Quotation created
        quote = Quotation.objects.filter(customer_id=cust_id).first()
        self.assertIsNotNone(quote)
        self.assertEqual(quote.customer.company_name, 'BHP Billiton Freight')
        self.assertEqual(quote.subtotal, 25000.00)

    def test_create_dynamic_payment_term_option(self):
        url = reverse('create_payment_term_option')
        payload = {
            'name': '25% Deposit / 75% on Delivery'
        }
        response = self.client.post(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['term']['name'], '25% Deposit / 75% on Delivery')
        term_code = data['term']['code']

        # Verify customer can be saved with this custom term
        cust = Customer.objects.create(
            company_name='Glencore Mining',
            contact_name='Chris Botha',
            email='chris.botha@glencore.com',
            phone='+27 11 772 0600',
            physical_address='Rustenburg',
            payment_terms=term_code
        )
        self.assertEqual(cust.get_payment_terms_display(), '25% Deposit / 75% on Delivery')

    def test_payment_term_options_api_list(self):
        url = reverse('payment_term_options_api')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('terms', data)
        self.assertGreaterEqual(len(data['terms']), 8)

