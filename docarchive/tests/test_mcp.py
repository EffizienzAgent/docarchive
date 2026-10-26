import inspect

import frappe
from frappe.tests import IntegrationTestCase

from docarchive import mcp, mcp_tools
from docarchive.tests.factories import (
	eml,
	make_business_document,
	make_document_type,
	make_file,
	make_manager,
	make_outsider,
	make_own_company,
	make_party,
	make_settings,
	party_fields,
)


def text_pdf(text):
	stream = b"BT /F1 12 Tf 20 100 Td (" + text.encode() + b") Tj ET"
	objects = [
		b"<< /Type /Catalog /Pages 2 0 R >>",
		b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
		b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R "
		b"/Resources << /Font << /F1 5 0 R >> >> >>",
		b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
		b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
	]
	out = b"%PDF-1.7\n"
	offsets = []
	for number, body in enumerate(objects, 1):
		offsets.append(len(out))
		out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
	xref = len(out)
	out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
	out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
	out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
	return out


class TestMcpDefinitions(IntegrationTestCase):
	def test_every_tool_matches_its_function(self):
		self.assertEqual(frappe.get_hooks("mcp_tools", app_name="docarchive"), mcp_tools.TOOLS)
		for definition in mcp_tools.TOOLS:
			schema = definition["input_schema"]
			self.assertLessEqual(set(schema["required"]), set(schema["properties"]), definition["name"])
			function = frappe.get_attr(definition["method"])
			parameters = inspect.signature(function).parameters
			accepts_any = any(p.kind == p.VAR_KEYWORD for p in parameters.values())
			named = {name for name, p in parameters.items() if p.kind != p.VAR_KEYWORD}
			self.assertTrue(set(schema["properties"]) <= named or accepts_any, definition["name"])
			self.assertTrue(named <= set(schema["properties"]), definition["name"])
			self.assertTrue(callable(frappe.get_attr(definition["available"])))

	def test_assign_arguments_cover_every_assignable_field(self):
		assign = next(t for t in mcp_tools.TOOLS if t["name"] == "assign_document")
		self.assertEqual(set(assign["input_schema"]["properties"]), {"name", "modified", *mcp.ASSIGNABLE})

	def test_registered_with_mcpserver_when_installed(self):
		if "mcpserver" not in frappe.get_installed_apps():
			self.skipTest("mcpserver is not installed")
		from mcpserver.tools import registry

		tools = registry()
		for definition in mcp_tools.TOOLS:
			self.assertIn(definition["name"], tools)


class TestMcpTools(IntegrationTestCase):
	def setUp(self):
		self.company = make_own_company().name
		make_settings(default_own_company=self.company, min_age_hours=24)
		self.type = make_document_type(number_required=1).name
		self.party = make_party()
		self.manager = make_manager("mcp-manager@example.com")
		frappe.share.add("Contact", self.party.name, self.manager, read=1)

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_list_open_documents_shows_missing_fields(self):
		open_doc = make_business_document(
			document_type=self.type, document_number=None, **party_fields(self.party)
		)
		complete = make_business_document(document_type=self.type, **party_fields(self.party))
		with self.set_user(self.manager):
			result = mcp.list_open_documents(limit=500)
		rows = {row["name"]: row for row in result["documents"]}
		self.assertEqual(rows[open_doc.name]["missing"], ["Document Number"])
		self.assertIn(open_doc.name, rows[open_doc.name]["url"])
		self.assertNotIn(complete.name, rows)
		self.assertEqual(result["total"], len(result["documents"]))
		self.assertEqual(len(mcp.list_open_documents(limit=1)["documents"]), 1)

	def test_get_document_reads_pdf_text(self):
		file_url = make_file(content=text_pdf("Rechnung 4711 Acme"), file_name="Rechnung.pdf").file_url
		doc = make_business_document(document_type=self.type, file=file_url, **party_fields(self.party))
		with self.set_user(self.manager):
			result = mcp.get_document(doc.name)
		self.assertIn("Rechnung 4711 Acme", result["text"])
		self.assertIsNone(result["text_note"])
		self.assertEqual(result["own_company"], self.company)
		self.assertIn("source_party_email", result)
		self.assertNotIn("text", mcp.get_document(doc.name, include_text=False))

	def test_get_document_reads_mail_and_explains_missing_text(self):
		mail = make_file(content=eml(Subject="Angebot 12"), file_name="mail.eml").file_url
		doc = make_business_document(
			document_type=self.type,
			file=mail,
			source_system="email",
			archive_write=True,
			**party_fields(self.party),
		)
		text = mcp.get_document(doc.name)["text"]
		self.assertIn("Subject: Angebot 12", text)
		self.assertIn("Hello", text)
		scan = make_business_document(document_type=self.type, **party_fields(self.party))
		result = mcp.get_document(scan.name)
		self.assertIsNone(result["text"])
		self.assertIn("text layer", result["text_note"])

	def test_get_document_needs_read_permission(self):
		doc = make_business_document(document_type=self.type, **party_fields(self.party))
		with self.set_user(make_outsider("mcp-outsider@example.com")):
			self.assertFalse(mcp.available())
			with self.assertRaises(frappe.PermissionError):
				mcp.get_document(doc.name)

	def test_document_options(self):
		with self.set_user(self.manager):
			options = mcp.document_options()
		types = {row.name: row for row in options["document_types"]}
		self.assertEqual(types[self.type].number_required, 1)
		self.assertIn(self.company, options["own_companies"])
		self.assertEqual(options["default_own_company"], self.company)

	def test_assign_completes_document_and_reports_archiving_time(self):
		doc = make_business_document(
			document_type=self.type, document_number=None, **party_fields(self.party)
		)
		with self.set_user(self.manager):
			result = mcp.assign_document(doc.name, document_number="RE-7", title="")
		self.assertEqual(result["missing"], [])
		self.assertEqual(result["document_number"], "RE-7")
		self.assertTrue(result["archives_after"])
		self.assertEqual(frappe.db.get_value("Business Document", doc.name, "archive_status"), "Pending")
		self.assertFalse(frappe.db.get_value("Business Document", doc.name, "archive_queued_at"))

	def test_assign_resolves_counterparty_by_title(self):
		doc = make_business_document(document_type=self.type)
		with self.set_user(self.manager):
			result = mcp.assign_document(
				doc.name, counterparty_type="Contact", counterparty=self.party.first_name
			)
		self.assertEqual(result["counterparty"], self.party.name)
		with self.set_user(self.manager), self.assertRaises(frappe.ValidationError):
			mcp.assign_document(doc.name, counterparty="nobody at all")

	def test_assign_refuses_stale_and_empty_calls(self):
		doc = make_business_document(
			document_type=self.type, document_number=None, **party_fields(self.party)
		)
		with self.set_user(self.manager):
			with self.assertRaises(frappe.ValidationError):
				mcp.assign_document(doc.name)
			with self.assertRaises(frappe.ValidationError):
				mcp.assign_document(doc.name, document_number="RE-8", modified="2000-01-01 00:00:00")
		self.assertIsNone(frappe.db.get_value("Business Document", doc.name, "document_number"))

	def test_assign_needs_write_permission(self):
		doc = make_business_document(
			document_type=self.type, document_number=None, **party_fields(self.party)
		)
		with self.set_user(make_outsider("mcp-writer-outsider@example.com")):
			with self.assertRaises(frappe.PermissionError):
				mcp.assign_document(doc.name, document_number="RE-9")
