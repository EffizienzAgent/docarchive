import json
from datetime import UTC, datetime

import frappe
from frappe.tests import IntegrationTestCase

from docarchive import keys, retention
from docarchive.tests.factories import make_document_type, make_own_company, make_settings


def doc(**fields):
	return frappe._dict(
		{
			"name": "DOC-2026-00001",
			"own_company": "acme-gmbh",
			"document_date": "2026-03-15",
			"document_number": "RE 2026/017",
			"counterparty": "ORG-00042",
			**fields,
		}
	)


class TestKeys(IntegrationTestCase):
	def test_key_layout(self):
		self.assertEqual(
			keys.object_key(doc(), "outgoing-invoice", "PDF"),
			"acme-gmbh/2026/outgoing-invoice/org-00042/2026-03-15_re-2026-017_doc-2026-00001.pdf",
		)

	def test_placeholders(self):
		self.assertEqual(
			keys.object_key(doc(counterparty=None, document_number=None), "bank-statement", "pdf"),
			"acme-gmbh/2026/bank-statement/ohne-gegenpartei/2026-03-15_ohne-nummer_doc-2026-00001.pdf",
		)

	def test_segments_are_sanitized(self):
		key = keys.object_key(
			doc(counterparty="../Müller & Söhne/..", document_number="  ä/ö\\ü:ß  "), "contract", "p/df"
		)
		self.assertEqual(
			key, "acme-gmbh/2026/contract/mueller-soehne/2026-03-15_ae-oe-ue-ss_doc-2026-00001.p-df"
		)
		self.assertNotIn("..", key)

	def test_slug(self):
		self.assertEqual(keys.slug("Ærøskøbing Café"), "aeroskobing-cafe")
		self.assertEqual(keys.slug("***", "fallback"), "fallback")
		self.assertEqual(len(keys.slug("a" * 200)), keys.MAX_SEGMENT)

	def test_meta_key(self):
		self.assertEqual(keys.meta_key("a/b.pdf"), "a/b.pdf.meta.json")

	def test_meta_bytes_are_deterministic_and_carry_the_mode(self):
		meta = {"b": 1, "a": "ä", "retention": {"retain_until": "2035-01-01T00:00:00Z"}}
		first = keys.meta_bytes(json.dumps(meta), "GOVERNANCE")
		self.assertEqual(first, keys.meta_bytes(meta, "GOVERNANCE"))
		self.assertEqual(json.loads(first)["retention"]["mode"], "GOVERNANCE")
		self.assertNotEqual(first, keys.meta_bytes(meta, "COMPLIANCE"))
		self.assertIn("ä".encode(), first)


class TestRetention(IntegrationTestCase):
	def setUp(self):
		self.company = make_own_company().name
		self.invoice = make_document_type(default_retention_years=8).name
		self.contract = make_document_type(
			retention_from="Contract End Date", default_retention_years=10
		).name
		make_settings(default_own_company=self.company, rules=[(self.company, self.invoice, 6)])

	def test_counts_from_end_of_document_year(self):
		until = retention.retain_until(doc(own_company=self.company, document_type=self.invoice))
		self.assertEqual(until, datetime(2033, 1, 1, tzinfo=UTC))

	def test_type_default_without_rule(self):
		other = make_own_company().name
		until = retention.retain_until(doc(own_company=other, document_type=self.invoice))
		self.assertEqual(until, datetime(2035, 1, 1, tzinfo=UTC))

	def test_contract_counts_from_contract_end(self):
		contract = doc(own_company=self.company, document_type=self.contract, contract_end_date="2030-06-30")
		self.assertEqual(retention.retain_until(contract), datetime(2041, 1, 1, tzinfo=UTC))
		contract.contract_end_date = None
		self.assertEqual(retention.retain_until(contract), datetime(2037, 1, 1, tzinfo=UTC))

	def test_contract_end_ignored_for_other_types(self):
		invoice = doc(own_company=self.company, document_type=self.invoice, contract_end_date="2030-06-30")
		self.assertEqual(retention.retain_until(invoice), datetime(2033, 1, 1, tzinfo=UTC))

	def test_missing_rule_raises(self):
		no_years = make_document_type(default_retention_years=0).name
		with self.assertRaises(frappe.ValidationError):
			retention.retain_until(doc(own_company=self.company, document_type=no_years))

	def test_db_round_trip_is_utc(self):
		value = datetime(2035, 1, 1, tzinfo=UTC)
		self.assertEqual(str(retention.to_db(value)), "2035-01-01 00:00:00")
		self.assertEqual(retention.from_db("2035-01-01 00:00:00"), value)
