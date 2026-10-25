import base64
import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from docarchive import api, content, einvoice, einvoice_parser, run, store
from docarchive.permissions import INTEGRATION_ROLE
from docarchive.tests.einvoice_fixtures import BUYER, SELLER, cii, ubl, zugferd
from docarchive.tests.factories import (
	make_business_document,
	make_document_type,
	make_file,
	make_own_company,
	make_settings,
	make_user,
	party_fields,
	unique,
)
from docarchive.tests.test_run import DOCUMENT, ArchiveRunTestCase


def clear_vat_ids():
	frappe.db.set_value("Own Company", {"vat_ids": ("is", "set")}, "vat_ids", None)


def xml_upload(data, name="rechnung.xml"):
	return make_file(data, file_name=f"{unique(name.rsplit('.', 1)[0])}.{name.rsplit('.', 1)[1]}")


def type_with_slug(slug, name):
	existing = frappe.db.get_value("Business Document Type", {"slug": slug}, "name")
	return existing or make_document_type(slug=slug, type_name=name).name


def hint_assign(doc):
	hint = einvoice.counterparty_hint(doc)
	if hint and not doc.counterparty:
		doc.counterparty_type = "Contact"
		doc.counterparty = frappe.db.get_value("Contact", {"first_name": hint["vat_id"] or hint["name"]})


def plugged_extractor(data, file_name):
	return {"syntax": "plugin", "number": "PLUG-1", "issue_date": "2026-02-01", "kind": "invoice"}


class TestParser(IntegrationTestCase):
	def test_cii(self):
		info = einvoice_parser.extract(cii())
		self.assertEqual(
			{
				k: info[k]
				for k in ("syntax", "number", "issue_date", "due_date", "type_code", "kind", "currency")
			},
			{
				"syntax": "CII",
				"number": "RE-2026-0042",
				"issue_date": "2026-03-10",
				"due_date": "2026-04-09",
				"type_code": "380",
				"kind": "invoice",
				"currency": "EUR",
			},
		)
		self.assertIn("xrechnung", info["profile"])
		self.assertEqual(info["buyer_reference"], "04011000-12345-34")
		self.assertEqual(
			info["totals"], {"net": "100.00", "tax": "19.00", "gross": "119.00", "due": "119.00"}
		)
		self.assertEqual(
			(info["seller"]["name"], info["seller"]["vat_ids"], info["seller"]["tax_numbers"]),
			(SELLER["name"], [SELLER["vat"]], [SELLER["tax"]]),
		)
		self.assertEqual(
			info["buyer"]["address"],
			{"line1": "Beispielweg 1", "line2": None, "postcode": "10115", "city": "Berlin", "country": "DE"},
		)

	def test_ubl_invoice(self):
		info = einvoice_parser.extract(ubl())
		self.assertEqual(
			(info["syntax"], info["number"], info["issue_date"], info["due_date"], info["kind"]),
			("UBL", "UBL-2026-7", "2026-03-11", "2026-04-10", "invoice"),
		)
		self.assertEqual(
			info["totals"], {"net": "200.00", "tax": "38.00", "gross": "238.00", "due": "238.00"}
		)
		self.assertEqual(
			(info["seller"]["name"], info["seller"]["vat_ids"], info["seller"]["tax_numbers"]),
			(SELLER["name"], [SELLER["vat"]], [SELLER["tax"]]),
		)
		self.assertEqual(info["buyer"]["address"]["city"], "Hamburg")
		self.assertEqual(info["buyer_reference"], "991-01234-56")

	def test_credit_notes(self):
		cases = {
			"ubl credit note": ubl(root="CreditNote"),
			"cii 381": cii(type_code="381"),
			"ubl invoice with credit code": ubl(type_code="381"),
		}
		for case, data in cases.items():
			with self.subTest(case):
				self.assertEqual(einvoice_parser.extract(data)["kind"], "credit_note")
		credit = einvoice_parser.extract(ubl(root="CreditNote"))
		self.assertEqual((credit["type_code"], credit["due_date"]), ("381", "2026-04-10"))

	def test_zugferd_pdf_is_read_without_change(self):
		pdf = zugferd(cii(number="ZF-1"))
		before = bytes(pdf)
		info = einvoice_parser.extract(pdf)
		self.assertEqual((info["number"], info["embedded_file"]), ("ZF-1", "factur-x.xml"))
		self.assertEqual(info["xml"], cii(number="ZF-1"))
		self.assertEqual(pdf, before)
		self.assertEqual(einvoice_parser.extract(zugferd(ubl(), name="xrechnung.xml"))["syntax"], "UBL")

	def test_not_an_invoice(self):
		cases = {
			"malformed": b"<rsm:CrossIndustryInvoice xmlns:rsm='x'><unclosed>",
			"other xml": b"<?xml version='1.0'?><note><to>x</to></note>",
			"text": b"just text",
			"empty": b"",
			"broken pdf": b"%PDF-1.7\nnot really",
			"pdf without xml": zugferd(b"plain", name="notes.txt"),
			"pdf with other xml": zugferd(b"<note/>", name="factur-x.xml"),
		}
		for case, data in cases.items():
			with self.subTest(case):
				self.assertIsNone(einvoice_parser.extract(data))

	def test_entities_and_dtds_are_refused(self):
		xxe = b"""<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]>
<rsm:CrossIndustryInvoice xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100">&x;</rsm:CrossIndustryInvoice>"""
		bomb = b"""<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]><r>&b;</r>"""
		for data in (xxe, bomb):
			self.assertIsNone(einvoice_parser.extract(data))

	def test_huge_files_are_skipped_before_parsing(self):
		huge = cii() + b" " * (einvoice_parser.MAX_XML_BYTES - len(cii()) + 1)
		with patch("defusedxml.ElementTree.fromstring") as parse:
			self.assertIsNone(einvoice_parser.extract(huge))
		parse.assert_not_called()
		with patch.object(einvoice_parser, "MAX_XML_BYTES", 1000):
			self.assertIsNone(einvoice_parser.extract(zugferd(cii())))
		self.assertIsNotNone(einvoice_parser.extract(zugferd(cii())))


class EInvoiceTestCase(IntegrationTestCase):
	def setUp(self):
		clear_vat_ids()
		self.own = make_own_company(vat_ids=f"{SELLER['vat']}\n").name
		self.default_company = make_own_company().name
		make_settings(default_own_company=self.default_company, store_einvoice_xml=0)
		self.outgoing = type_with_slug("outgoing-invoice", "Outgoing Invoice")
		self.incoming = type_with_slug("incoming-invoice", "Incoming Invoice")
		self.other_type = make_document_type(counterparty_required=0).name

	def document(self, data, name="rechnung.xml", **fields):
		return make_business_document(
			file=xml_upload(data, name).file_url, **{"document_type": self.other_type, **fields}
		)


class TestPrefill(EInvoiceTestCase):
	def blank(self, file_url, **fields):
		doc = frappe.get_doc({"doctype": DOCUMENT, "file": file_url, **fields})
		return doc.insert(ignore_permissions=True)

	def test_empty_fields_are_prefilled(self):
		doc = self.blank(xml_upload(cii()).file_url)
		self.assertEqual(
			(str(doc.document_date), doc.document_number, doc.document_type, doc.own_company),
			("2026-03-10", "RE-2026-0042", self.outgoing, self.own),
		)
		self.assertEqual(doc.title, f"Invoice RE-2026-0042 {BUYER['name']}")
		data = json.loads(doc.einvoice_data)
		self.assertEqual((data["direction"], data["source"], data["conflicts"]), ("outgoing", "main", []))

	def test_buyer_match_is_incoming(self):
		frappe.db.set_value("Own Company", self.own, "vat_ids", BUYER["vat"].lower().replace("de", "DE "))
		doc = self.blank(xml_upload(ubl(root="CreditNote")).file_url)
		self.assertEqual((doc.document_type, doc.own_company), (self.incoming, self.own))
		self.assertEqual(doc.title, f"Credit Note UBL-2026-7 {SELLER['name']}")

	def test_tax_numbers_match_too(self):
		frappe.db.set_value("Own Company", self.own, "vat_ids", "201 / 113 / 40209")
		self.assertEqual(self.blank(xml_upload(cii()).file_url).document_type, self.outgoing)

	def test_unknown_parties_leave_type_and_company_alone(self):
		frappe.db.set_value("Own Company", self.own, "vat_ids", "")
		doc = self.document(cii())
		self.assertEqual(doc.document_type, self.other_type)
		self.assertIsNone(json.loads(doc.einvoice_data)["direction"])
		self.assertIsNone(einvoice.counterparty_hint(doc))

	def test_explicit_values_are_never_overwritten(self):
		other = make_own_company().name
		doc = self.document(
			cii(),
			document_date="2026-03-12",
			document_number="MY-1",
			own_company=other,
			title="Kept",
		)
		self.assertEqual(
			(str(doc.document_date), doc.document_number, doc.document_type, doc.own_company, doc.title),
			("2026-03-12", "MY-1", self.other_type, other, "Kept"),
		)
		conflicts = {c["field"]: c["xml"] for c in json.loads(doc.einvoice_data)["conflicts"]}
		self.assertEqual(
			conflicts,
			{
				"document_date": "2026-03-10",
				"document_number": "RE-2026-0042",
				"document_type": self.outgoing,
				"own_company": self.own,
			},
		)
		doc.document_date = "2026-03-10"
		doc.save(ignore_permissions=True)
		conflicts = {c["field"] for c in json.loads(doc.einvoice_data)["conflicts"]}
		self.assertNotIn("document_date", conflicts)

	def test_data_is_server_only(self):
		doc = self.document(cii())
		doc.einvoice_data = json.dumps({"number": "forged"})
		doc.save(ignore_permissions=True)
		self.assertEqual(json.loads(doc.reload().einvoice_data)["number"], "RE-2026-0042")

	def test_found_in_an_attachment(self):
		doc = make_business_document(
			document_type=self.other_type,
			attachments=[{"file": xml_upload(ubl()).file_url, "role": "Structured Invoice (XML)"}],
		)
		data = json.loads(doc.einvoice_data)
		self.assertEqual((data["number"], data["source"]), ("UBL-2026-7", "attachment 1"))

	def test_plain_documents_have_no_data(self):
		doc = make_business_document(document_type=self.other_type)
		self.assertIsNone(doc.einvoice_data)
		self.assertIsNone(self.document(b"<note/>").einvoice_data)
		self.assertIsNone(self.document(b"<broken").einvoice_data)

	def test_extractor_hook_wins(self):
		with self.patch_hooks(
			{"docarchive_einvoice_extractors": ["docarchive.tests.test_einvoice.plugged_extractor"]}
		):
			doc = self.document(cii(), document_number=None)
		self.assertEqual(doc.document_number, "PLUG-1")
		self.assertEqual(json.loads(doc.einvoice_data)["syntax"], "plugin")

	def test_counterparty_hint_reaches_the_assign_hook(self):
		party = frappe.get_doc({"doctype": "Contact", "first_name": BUYER["vat"]}).insert(
			ignore_permissions=True
		)
		with self.patch_hooks({"docarchive_assign": ["docarchive.tests.test_einvoice.hint_assign"]}):
			doc = self.document(cii())
		self.assertEqual((doc.counterparty_type, doc.counterparty), ("Contact", party.name))
		hint = einvoice.counterparty_hint(doc)
		self.assertEqual((hint["vat_id"], hint["name"]), (BUYER["vat"], BUYER["name"]))

	def test_xml_of_a_zugferd_pdf_is_not_stored_by_default(self):
		doc = self.document(zugferd(), name="rechnung.pdf")
		self.assertEqual(doc.attachments, [])
		self.assertEqual(json.loads(doc.einvoice_data)["embedded_file"], "factur-x.xml")

	def test_xml_of_a_zugferd_pdf_can_be_stored(self):
		make_settings(default_own_company=self.default_company, store_einvoice_xml=1)
		pdf = zugferd(cii(number=unique("ZF")))
		doc = self.document(pdf, name="rechnung.pdf")
		[row] = doc.attachments
		self.assertEqual(row.role, "Structured Invoice (XML)")
		self.assertEqual(content.read_bytes(row.file), einvoice_parser.extract(pdf)["xml"])
		self.assertEqual(content.read_bytes(doc.file), pdf)
		file = frappe.get_doc("File", {"file_url": row.file, "attached_to_name": doc.name})
		self.assertEqual(file.is_private, 1)
		doc.save(ignore_permissions=True)
		self.assertEqual(len(doc.reload().attachments), 1)


class TestEInvoiceArchive(ArchiveRunTestCase):
	def test_meta_includes_the_einvoice_data(self):
		clear_vat_ids()
		make_own_company(vat_ids=SELLER["vat"])
		doc = self.document(file=xml_upload(cii()).file_url)
		self.assertEqual(run.archive_document(doc.name, store.targets()), "Archived")
		doc.reload()
		meta = json.loads(self.fake("primary").body("archive-primary", doc.meta_key))
		self.assertEqual(meta["einvoice"]["number"], "RE-2026-0042")
		self.assertEqual(meta["einvoice"]["totals"]["gross"], "119.00")
		self.assertEqual(meta["einvoice"], json.loads(doc.einvoice_data))

	def test_plain_meta_has_no_einvoice(self):
		doc = self.document()
		run.archive_document(doc.name, store.targets())
		meta = json.loads(
			self.fake("primary").body("archive-primary", frappe.get_doc(DOCUMENT, doc.name).meta_key)
		)
		self.assertIsNone(meta["einvoice"])

	def test_data_is_frozen_after_archive_start(self):
		doc = self.document(file=xml_upload(cii()).file_url)
		run.archive_document(doc.name, store.targets())
		doc.reload()
		before = doc.einvoice_data
		frappe.db.set_value("Own Company", doc.own_company, "vat_ids", SELLER["vat"])
		doc.title = "Changed"
		doc.save(ignore_permissions=True)
		self.assertEqual(doc.reload().einvoice_data, before)


class TestEInvoiceUpload(EInvoiceTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user(make_user("docarchive-einvoice@example.com", [INTEGRATION_ROLE]))
		self.addCleanup(frappe.set_user, "Administrator")

	def upload(self, data, name="invoice.xml", **fields):
		return api.upload_document(file_name=name, content_base64=base64.b64encode(data).decode(), **fields)

	def test_type_date_and_number_come_from_the_einvoice(self):
		result = self.upload(zugferd(cii(number=unique("ZF"))), name="invoice.pdf")
		frappe.set_user("Administrator")
		doc = frappe.get_doc(DOCUMENT, result["name"])
		self.assertEqual(
			(doc.document_type, str(doc.document_date), doc.own_company),
			(self.outgoing, "2026-03-10", self.own),
		)
		self.assertTrue(doc.document_number.startswith("ZF"))

	def test_explicit_fields_win(self):
		result = self.upload(
			cii(number=unique("RE")),
			document_type=frappe.db.get_value("Business Document Type", self.other_type, "slug"),
			document_date="2026-03-01",
		)
		frappe.set_user("Administrator")
		doc = frappe.get_doc(DOCUMENT, result["name"])
		self.assertEqual((doc.document_type, str(doc.document_date)), (self.other_type, "2026-03-01"))
		self.assertIn("document_date", {c["field"] for c in json.loads(doc.einvoice_data)["conflicts"]})

	def test_plain_files_still_need_type_and_date(self):
		for fields in ({"document_date": "2026-03-01"}, {"document_type": "outgoing-invoice"}):
			with self.subTest(fields), self.assertRaises(api.UploadError):
				self.upload(b"%PDF-1.7 plain", name="scan.pdf", **fields)


class TestBackfill(EInvoiceTestCase):
	def setUp(self):
		super().setUp()
		self.enterContext(patch.object(frappe.db, "commit"))

	def old_document(self, data, **fields):
		doc = self.document(data, **fields)
		frappe.db.set_value(DOCUMENT, doc.name, {"einvoice_data": None, "document_number": None})
		return doc.name

	def test_dry_run_lists_and_writes_nothing(self):
		name = self.old_document(cii(number="BF-1"), document_date="2026-03-12")
		result = einvoice.backfill(dry_run=True)
		[found] = [row for row in result["found"] if row["name"] == name]
		self.assertEqual(found["fills"], {"document_number": "BF-1"})
		self.assertIsNone(frappe.db.get_value(DOCUMENT, name, "einvoice_data"))

	def test_fills_only_empty_fields(self):
		name = self.old_document(cii(number="BF-2"), document_date="2026-03-12")
		started = self.old_document(cii(number="BF-3"))
		frappe.db.set_value(DOCUMENT, started, "archive_started_at", frappe.utils.now_datetime())
		result = einvoice.backfill()
		self.assertEqual(result["errors"], [])
		doc = frappe.get_doc(DOCUMENT, name)
		self.assertEqual((doc.document_number, str(doc.document_date)), ("BF-2", "2026-03-12"))
		self.assertIn("document_date", {c["field"] for c in json.loads(doc.einvoice_data)["conflicts"]})
		self.assertNotIn(started, [row["name"] for row in result["found"]])
		self.assertIsNone(frappe.db.get_value(DOCUMENT, started, "einvoice_data"))

	def test_command_is_registered(self):
		from docarchive.commands import commands

		self.assertIn("docarchive-extract", [command.name for command in commands])


class TestDialogDate(EInvoiceTestCase):
	def test_date_comes_from_the_einvoice(self):
		from docarchive import documents

		party = frappe.get_doc({"doctype": "Contact", "first_name": unique("party")}).insert(
			ignore_permissions=True
		)
		[name] = documents.create_documents(
			"Contact", party.name, json.dumps([xml_upload(cii()).file_url]), self.other_type
		)
		self.assertEqual(str(frappe.db.get_value(DOCUMENT, name, "document_date")), "2026-03-10")
