import base64
import io
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from docarchive import api
from docarchive.permissions import INTEGRATION_ROLE, MANAGER_ROLE
from docarchive.tests.factories import (
	make_business_document,
	make_document_type,
	make_own_company,
	make_party,
	make_settings,
	make_user,
	pdf,
	unique,
)

INTEGRATION_USER = "docarchive-integration@example.com"


def assign_by_party_id(doc):
	if doc.source_system == "Billing" and doc.source_party_id and not doc.counterparty:
		doc.counterparty_type = "Contact"
		doc.counterparty = frappe.db.get_value("Contact", {"first_name": doc.source_party_id})


class TestUploadApi(IntegrationTestCase):
	def setUp(self):
		self.company = make_own_company().name
		make_settings(default_own_company=self.company)
		self.type = make_document_type(number_required=1)
		self.user = make_user(INTEGRATION_USER, [INTEGRATION_ROLE])
		frappe.set_user(self.user)
		self.addCleanup(frappe.set_user, "Administrator")
		self.addCleanup(setattr, frappe.local, "request", None)

	def upload(self, data=None, **fields):
		data = pdf(unique("body").encode()) if data is None else data
		payload = {
			"document_type": self.type.slug,
			"document_date": "2026-03-15",
			"document_number": "INV-1",
			"file_name": "invoice.pdf",
			"content_base64": base64.b64encode(data).decode(),
			"source_party_id": "7",
			**fields,
		}
		return api.upload_document(**{k: v for k, v in payload.items() if v is not None})

	def multipart(self, data, file_name="invoice.pdf"):
		environ = EnvironBuilder(
			method="POST", data={"file": (io.BytesIO(data), file_name)}, path="/api/method/x"
		).get_environ()
		frappe.local.request = Request(environ)
		frappe.local.request_ip = "127.0.0.1"

	def test_role_installed_without_desk_or_document_rights(self):
		self.assertEqual(frappe.db.get_value("Role", INTEGRATION_ROLE, "desk_access"), 0)
		self.assertFalse(frappe.has_permission("Business Document", "read"))
		self.assertFalse(frappe.has_permission("Business Document", "create"))

	def test_endpoint_is_post_only(self):
		self.assertIn(api.upload_document, frappe.whitelisted)
		self.assertEqual(frappe.allowed_http_methods_for_whitelisted_func[api.upload_document], ["POST"])

	def test_upload_creates_private_document(self):
		data = pdf(b"create")
		result = self.upload(data, title="March invoice", source_key=unique("billing:invoice"))
		frappe.set_user("Administrator")
		doc = frappe.get_doc("Business Document", result["name"])
		self.assertEqual(result["status"], "created")
		self.assertEqual(
			(doc.document_type, str(doc.document_date), doc.document_number, doc.title, doc.own_company),
			(self.type.name, "2026-03-15", "INV-1", "March invoice", self.company),
		)
		self.assertEqual(result["content_sha256"], doc.content_sha256)
		file = frappe.get_doc("File", {"file_url": doc.file, "attached_to_name": doc.name})
		self.assertEqual((file.is_private, file.attached_to_doctype), (1, "Business Document"))
		self.assertEqual(file.get_content(), data)

	def test_source_fields_are_set_server_side(self):
		result = self.upload(
			source_system="Billing",
			source_account="acct-1",
			source_party_type="client",
			source_party_name="Acme Ltd",
			source_party_email="billing@acme.example.test",
		)
		doc = frappe.get_doc("Business Document", result["name"])
		self.assertEqual(
			[doc.get(f) for f in ("source_system", "source_account", "source_party_type", "source_party_id")],
			["Billing", "acct-1", "client", "7"],
		)
		self.assertEqual(
			(doc.source_party_name, doc.source_party_email), ("Acme Ltd", "billing@acme.example.test")
		)

	def test_assign_hook_resolves_counterparty(self):
		party = make_party()
		hook = "docarchive.tests.test_api.assign_by_party_id"
		with self.patch_hooks({"docarchive_assign": [hook]}):
			result = self.upload(source_system="Billing", source_party_id=party.first_name)
			unknown = self.upload(source_system="Billing", source_party_id="nobody")
		self.assertEqual((result["counterparty_type"], result["counterparty"]), ("Contact", party.name))
		self.assertIsNone(unknown["counterparty"])
		self.assertEqual(unknown["missing"], ["Counterparty"])

	def test_multipart_equals_base64(self):
		data = pdf(b"multipart")
		self.multipart(data)
		result = api.upload_document(
			document_type=self.type.slug, document_date="2026-03-15", document_number="1", source_party_id="7"
		)
		self.assertEqual(result["status"], "created")
		frappe.local.request = None
		again = self.upload(data, document_number="1")
		self.assertEqual((again["status"], again["name"]), ("exists", result["name"]))

	def test_same_content_for_same_type_returns_existing(self):
		data = pdf(b"twice")
		first = self.upload(data)
		second = self.upload(data, source_key=unique("other"))
		self.assertEqual((second["status"], second["name"]), ("exists", first["name"]))
		other_type = make_document_type()
		third = self.upload(data, document_type=other_type.slug)
		self.assertEqual(third["status"], "created")

	def test_same_source_key_returns_existing(self):
		key = unique("billing:invoice")
		first = self.upload(pdf(b"key"), source_key=key)
		again = self.upload(pdf(b"key"), source_key=key)
		self.assertEqual((again["status"], again["name"]), ("exists", first["name"]))
		self.assertEqual(frappe.db.count("Business Document", {"source_key": key}), 1)

	def test_changed_file_under_same_source_key_creates_new_version(self):
		key = unique("billing:invoice")
		first = self.upload(pdf(b"v1"), source_key=key)
		frappe.set_user("Administrator")
		old = frappe.get_doc("Business Document", first["name"])
		old.flags.archive_write = True
		old.archive_started_at = frappe.utils.now_datetime()
		old.save(ignore_permissions=True)
		frappe.set_user(self.user)
		second = self.upload(pdf(b"v2"), source_key=key)
		third = self.upload(pdf(b"v3"), source_key=key)
		self.assertEqual((second["status"], second["replaces"]), ("replaced", first["name"]))
		self.assertEqual((third["status"], third["replaces"]), ("replaced", second["name"]))
		self.assertEqual(self.upload(pdf(b"v2"), source_key=key)["name"], second["name"])
		self.assertEqual(self.upload(pdf(b"v1"), source_key=key)["name"], first["name"])
		old.reload()
		self.assertEqual((old.content_sha256, old.source_key), (first["content_sha256"], key))

	def test_document_date_required_and_iso(self):
		for value in (None, "", "15.03.2026", "2026-02-30"):
			with self.subTest(value=value), self.assertRaises(api.UploadError):
				self.upload(document_date=value)

	def test_unknown_type_rejected(self):
		for value in (None, "no-such-type"):
			with self.subTest(value=value), self.assertRaises(api.UploadError):
				self.upload(document_type=value)

	def test_type_required_fields(self):
		with self.assertRaisesRegex(api.UploadError, "Document Number"):
			self.upload(document_number=None)
		with self.assertRaisesRegex(api.UploadError, "counterparty"):
			self.upload(source_party_id=None)
		contract = make_document_type(retention_from="Contract End Date", contract_end_required=1)
		with self.assertRaisesRegex(api.UploadError, "Contract End Date"):
			self.upload(document_type=contract.slug)
		result = self.upload(document_type=contract.slug, contract_end_date="2027-12-31")
		self.assertEqual(
			str(frappe.db.get_value("Business Document", result["name"], "contract_end_date")), "2027-12-31"
		)

	def test_file_rejections(self):
		cases = {
			"disallowed extension": {"file_name": "run.exe"},
			"no extension": {"file_name": "invoice"},
			"slash in name": {"file_name": "../invoice.pdf"},
			"no file": {"content_base64": None},
			"bad base64": {"content_base64": "not base64!"},
			"empty": {"content_base64": ""},
			"broken pdf": {"content_base64": base64.b64encode(b"%PDF-1.4 broken").decode()},
		}
		for case, fields in cases.items():
			with self.subTest(case), self.assertRaises(api.UploadError):
				self.upload(**fields)
		self.assertEqual(api.UploadError.http_status_code, 400)

	def test_size_limit(self):
		with patch("docarchive.api.get_max_file_size", return_value=100):
			with self.assertRaisesRegex(api.UploadError, "larger"):
				self.upload(b"x" * 101)

	def test_own_company_hint(self):
		other = make_own_company().name
		self.assertEqual(self.upload(own_company=other)["own_company"], other)
		disabled = make_own_company(disabled=1).name
		for value in (disabled, "no-such-company"):
			with self.subTest(value=value), self.assertRaises(api.UploadError):
				self.upload(own_company=value)

	def test_forbidden_without_role(self):
		frappe.set_user(make_user("docarchive-no-role@example.com", [MANAGER_ROLE]))
		with self.assertRaises(frappe.PermissionError):
			self.upload()

	def test_upload_never_changes_an_archived_document(self):
		key = unique("billing:invoice")
		frappe.set_user("Administrator")
		archived = make_business_document(
			archive_write=True, source_key=key, archive_started_at=frappe.utils.now_datetime()
		)
		frappe.set_user(self.user)
		result = self.upload(pdf(b"new"), source_key=key)
		self.assertEqual(result["replaces"], archived.name)
		before = archived.as_dict()
		archived.reload()
		self.assertEqual(str(archived.modified), str(before.modified))
