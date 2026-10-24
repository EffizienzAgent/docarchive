import hashlib

import frappe
import frappe.utils.response as response
from frappe.tests import IntegrationTestCase
from frappe.utils import now_datetime
from werkzeug.exceptions import Forbidden
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from docarchive import content
from docarchive.document_archive.doctype.business_document.business_document import (
	LOCKED_FIELDS,
	SOURCE_FIELDS,
	missing_fields,
	title_from_file_name,
)
from docarchive.tests.factories import (
	PDF,
	make_business_document,
	make_document_type,
	make_file,
	make_manager,
	make_outsider,
	make_own_company,
	make_party,
	make_settings,
	party_fields,
	pdf,
	unique,
)


def start_archive(doc):
	frappe.db.set_value("Business Document", doc.name, "archive_started_at", now_datetime())
	return frappe.get_doc("Business Document", doc.name)


class BusinessDocumentTestCase(IntegrationTestCase):
	def setUp(self):
		self.company = make_own_company("acme-gmbh").name
		make_settings(default_own_company=self.company)
		self.invoice = make_document_type(number_required=1).name
		self.contract = make_document_type(
			retention_from="Contract End Date", default_retention_years=10
		).name

	def tearDown(self):
		frappe.set_user("Administrator")


class TestBusinessDocument(BusinessDocumentTestCase):
	def test_own_company_defaults_from_settings(self):
		self.assertEqual(make_business_document().own_company, self.company)

	def test_missing_own_company_rejected(self):
		make_settings()
		with self.assertRaises(frappe.ValidationError):
			make_business_document()

	def test_counterparty_needs_type(self):
		with self.assertRaises(frappe.ValidationError):
			make_business_document(counterparty=make_party().name)

	def test_counterparty_must_exist(self):
		with self.assertRaises(frappe.LinkValidationError):
			make_business_document(counterparty_type="Contact", counterparty="missing-contact")

	def test_user_needs_read_on_counterparty(self):
		party = make_party()
		user = make_manager("archive-party-reader@example.com")

		def insert():
			return frappe.get_doc(
				{
					"doctype": "Business Document",
					"document_type": self.invoice,
					"document_date": "2026-03-15",
					"file": make_file().file_url,
					**party_fields(party),
				}
			).insert()

		with self.set_user(user), self.assertRaises(frappe.PermissionError):
			insert()
		frappe.share.add("Contact", party.name, user, read=1)
		with self.set_user(user):
			doc = insert()
		frappe.share.remove("Contact", party.name, user)
		other = make_party("Other").name
		with self.set_user(user):
			doc = frappe.get_doc("Business Document", doc.name)
			doc.title = "Still editable"
			doc.save()
			doc.counterparty = other
			with self.assertRaises(frappe.PermissionError):
				doc.save()

	def test_counterparty_doctypes_hook_limits_types(self):
		party = make_party()
		with self.patch_hooks({"docarchive_counterparty_doctypes": ["User"]}):
			with self.assertRaises(frappe.ValidationError):
				make_business_document(**party_fields(party))
		with self.patch_hooks({"docarchive_counterparty_doctypes": ["Contact"]}):
			self.assertEqual(make_business_document(**party_fields(party)).counterparty, party.name)

	def test_child_table_cannot_be_counterparty(self):
		with self.assertRaises(frappe.ValidationError):
			make_business_document(counterparty_type="Has Role", counterparty="x")

	def test_contract_end_date_only_for_contract_end_types(self):
		with self.assertRaises(frappe.ValidationError):
			make_business_document(document_type=self.invoice, contract_end_date="2030-12-31")
		doc = make_business_document(document_type=self.contract, contract_end_date="2030-12-31")
		self.assertEqual(str(doc.contract_end_date), "2030-12-31")

	def test_contract_end_before_document_date_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			make_business_document(
				document_type=self.contract, document_date="2026-03-15", contract_end_date="2026-03-14"
			)

	def test_content_sha256_set_from_file(self):
		doc = make_business_document(file=make_file(PDF).file_url)
		self.assertEqual(doc.content_sha256, hashlib.sha256(PDF).hexdigest())
		other = pdf(b"other")
		doc.file = make_file(other).file_url
		doc.save()
		self.assertEqual(doc.content_sha256, hashlib.sha256(other).hexdigest())

	def test_missing_fields_follow_type(self):
		doc = make_business_document(document_type=self.invoice, document_number=None)
		self.assertEqual(missing_fields(doc), ["Counterparty", "Document Number"])
		doc.update(party_fields(make_party()))
		doc.document_number = "INV-1"
		self.assertEqual(missing_fields(doc), [])
		required_end = make_document_type(
			retention_from="Contract End Date", contract_end_required=1, counterparty_required=0
		).name
		doc = make_business_document(document_type=required_end, file=None)
		self.assertEqual(missing_fields(doc), ["File", "Contract End Date"])


class TestBusinessDocumentLock(BusinessDocumentTestCase):
	def test_locked_fields_rejected_after_archive_start(self):
		user = frappe.get_doc("User", make_manager(f"{unique('lock')}@example.com"))
		values = {
			"own_company": make_own_company("acme-ltd").name,
			"document_type": make_document_type().name,
			"document_date": "2026-04-01",
			"document_number": "INV-2026-999",
			"counterparty_type": "User",
			"counterparty": user.name,
			"file": make_file().file_url,
			**{field: "changed" for field in SOURCE_FIELDS},
		}
		self.assertEqual(set(values), set(LOCKED_FIELDS))
		for field, value in values.items():
			with self.subTest(field=field):
				doc = start_archive(make_business_document(**party_fields(make_party())))
				if field == "counterparty_type":
					doc.counterparty = user.name
				doc.flags.archive_write = field in SOURCE_FIELDS
				doc.set(field, value)
				with self.assertRaises(frappe.ValidationError):
					doc.save()

	def test_archive_start_cannot_be_cleared(self):
		doc = start_archive(make_business_document(**party_fields(make_party())))
		doc.archive_started_at = None
		doc.save()
		self.assertTrue(frappe.db.get_value("Business Document", doc.name, "archive_started_at"))

	def test_db_set_refuses_locked_fields_after_archive_start(self):
		doc = start_archive(make_business_document(**party_fields(make_party())))
		for field, value in (("document_number", "INV-X"), ("archive_started_at", None)):
			with self.subTest(field=field), self.assertRaises(frappe.ValidationError):
				doc.db_set(field, value)
		doc.db_set({"title": "New"})
		doc.flags.archive_write = True
		doc.db_set("document_number", "INV-Y")
		self.assertEqual(frappe.db.get_value("Business Document", doc.name, "document_number"), "INV-Y")

	def test_archived_party_cannot_be_renamed_or_merged(self):
		party = make_party()
		start_archive(make_business_document(**party_fields(party)))
		with self.assertRaises(frappe.ValidationError):
			frappe.rename_doc("Contact", party.name, f"{party.name}-new")
		with self.assertRaises(frappe.ValidationError):
			frappe.rename_doc("Contact", party.name, make_party("Target").name, merge=True)
		self.assertTrue(frappe.db.exists("Contact", party.name))
		free = make_party("Free")
		make_business_document(**party_fields(free))
		renamed = frappe.rename_doc("Contact", free.name, f"{free.name}-new")
		self.assertTrue(frappe.db.exists("Business Document", {"counterparty": renamed}))


FORGED = {
	"archive_status": "Archived",
	"archive_started_at": "2026-01-01 00:00:00",
	"archived_at": "2026-01-01 00:00:00",
	"archive_attempts": 3,
	"archive_error": "forged",
	"archive_sha256": "f" * 64,
	"archive_size": 1,
	"object_key": "forged/key.pdf",
	"meta_key": "forged/key.pdf.meta.json",
	"retention_mode": "COMPLIANCE",
	"retain_until": "2099-01-01 00:00:00",
	"source_key": "forged",
	"content_sha256": "0" * 64,
	"source_system": "forged",
	"source_account": "forged",
	"source_party_type": "forged",
	"source_party_id": "forged",
}


class TestBusinessDocumentProtectedFields(BusinessDocumentTestCase):
	def test_archive_fields_ignored_on_insert(self):
		replaced = make_business_document().name
		doc = make_business_document(replaces=replaced, **FORGED)
		saved = frappe.get_doc("Business Document", doc.name)
		self.assertEqual(saved.archive_status, "Pending")
		self.assertNotEqual(saved.content_sha256, "0" * 64)
		for field in [*FORGED, "replaces"]:
			if field not in ("archive_status", "content_sha256"):
				self.assertFalse(saved.get(field), field)

	def test_archive_fields_ignored_on_update(self):
		doc = make_business_document()
		original = doc.content_sha256
		doc.update(FORGED)
		doc.replaces = make_business_document().name
		doc.save()
		saved = frappe.get_doc("Business Document", doc.name)
		self.assertEqual(saved.archive_status, "Pending")
		self.assertEqual(saved.content_sha256, original)
		self.assertFalse(saved.replaces)
		self.assertFalse(saved.retain_until)

	def test_server_flag_sets_source_fields(self):
		doc = make_business_document(archive_write=True, source_system="Billing", source_party_id="7")
		self.assertEqual((doc.source_system, doc.source_party_id), ("Billing", "7"))
		doc.flags.archive_write = False
		doc.source_party_id = "8"
		doc.save()
		self.assertEqual(frappe.db.get_value("Business Document", doc.name, "source_party_id"), "7")

	def test_server_flag_allows_archive_fields(self):
		doc = make_business_document()
		doc.flags.archive_write = True
		doc.archive_status = "Failed"
		doc.archive_error = "Bucket unreachable"
		doc.save()
		self.assertEqual(frappe.db.get_value("Business Document", doc.name, "archive_status"), "Failed")

	def test_contract_end_date_and_title_editable_after_archive_start(self):
		doc = start_archive(make_business_document(document_type=self.contract, **party_fields(make_party())))
		doc.contract_end_date = "2031-06-30"
		doc.title = "Framework contract"
		doc.save()
		self.assertEqual(str(doc.contract_end_date), "2031-06-30")

	def test_unchanged_save_after_archive_start_passes(self):
		doc = start_archive(make_business_document(**party_fields(make_party())))
		doc.save()

	def test_delete_refused_after_archive_start(self):
		doc = start_archive(make_business_document(**party_fields(make_party())))
		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc("Business Document", doc.name)
		deletable = make_business_document()
		frappe.delete_doc("Business Document", deletable.name)
		self.assertFalse(frappe.db.exists("Business Document", deletable.name))


class TestBusinessDocumentTitle(BusinessDocumentTestCase):
	def test_title_from_file_name(self):
		self.assertEqual(title_from_file_name("Invoice_2026-03 acme.pdf"), "Invoice 2026 03 acme")
		self.assertEqual(title_from_file_name("contract"), "contract")
		self.assertEqual(title_from_file_name("scan.v2.PDF"), "scan.v2")

	def test_title_defaults_from_file_name(self):
		doc = make_business_document(file=make_file(file_name="Framework_contract-acme.pdf").file_url)
		self.assertEqual(doc.title, "Framework contract acme")

	def test_title_follows_new_file_until_edited(self):
		doc = make_business_document(file=make_file(file_name="first_draft.pdf").file_url)
		doc.file = make_file(file_name="second_draft.pdf").file_url
		doc.save()
		self.assertEqual(doc.title, "second draft")

	def test_user_title_never_overwritten(self):
		doc = make_business_document(title="My title")
		self.assertEqual(doc.title, "My title")
		doc.file = make_file(file_name="other_file.pdf").file_url
		doc.save()
		self.assertEqual(doc.title, "My title")

	def test_title_falls_back_to_number_then_type_without_file(self):
		doc = make_business_document(file=None, document_number="INV-2026-017")
		self.assertEqual(doc.title, "INV-2026-017")
		doc = make_business_document(file=None, document_number=None, document_type=self.contract)
		self.assertEqual(doc.title, self.contract)
		doc.file = make_file(file_name="signed.pdf").file_url
		doc.save()
		self.assertEqual(doc.title, "signed")


class TestBusinessDocumentFiles(BusinessDocumentTestCase):
	def request(self, path):
		saved = getattr(frappe.local, "request", None)
		self.addCleanup(setattr, frappe.local, "request", saved)
		frappe.local.request = Request(EnvironBuilder(path=path).get_environ())

	def test_attached_file_is_private_and_on_the_document(self):
		party = make_party()
		doc = make_business_document(**party_fields(party))
		files = frappe.get_all(
			"File",
			filters={"file_url": doc.file},
			fields=["attached_to_doctype", "attached_to_name", "attached_to_field", "is_private"],
		)
		self.assertEqual(
			files,
			[
				{
					"attached_to_doctype": "Business Document",
					"attached_to_name": doc.name,
					"attached_to_field": "file",
					"is_private": 1,
				}
			],
		)
		self.assertFalse(
			frappe.db.exists("File", {"attached_to_doctype": "Contact", "attached_to_name": party.name})
		)

	def test_public_file_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			make_business_document(file=make_file(is_private=0).file_url)

	def test_file_attached_to_other_record_rejected(self):
		party = make_party()
		attached = make_file(attached_to_doctype="Contact", attached_to_name=party.name)
		with self.assertRaises(frappe.ValidationError):
			make_business_document(file=attached.file_url)

	def test_same_bytes_attached_elsewhere(self):
		data = pdf(b"shared")
		party = make_party()
		elsewhere = make_file(
			data, file_name="mail_attachment.pdf", attached_to_doctype="Contact", attached_to_name=party.name
		)
		frappe.set_user(make_manager("archive-dedupe@example.com"))
		upload = make_file(data, file_name="Invoice_Acme.pdf")
		self.assertEqual(upload.file_url, elsewhere.file_url)
		doc = make_business_document(file=upload.file_url)
		self.assertEqual(doc.title, "Invoice Acme")
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("File", elsewhere.name, "attached_to_doctype"), "Contact")
		self.assertEqual(
			frappe.db.get_value("File", upload.name, ["attached_to_doctype", "attached_to_name"]),
			("Business Document", doc.name),
		)

	def test_file_of_another_user_rejected(self):
		frappe.set_user(make_manager("archive-uploader@example.com"))
		foreign = make_file()
		frappe.set_user(make_manager("archive-manager@example.com"))
		with self.assertRaises(frappe.ValidationError):
			make_business_document(file=foreign.file_url)

	def test_manager_can_upload_and_read(self):
		frappe.set_user(make_manager("archive-doc-manager@example.com"))
		upload = make_file()
		doc = frappe.get_doc(
			{
				"doctype": "Business Document",
				"document_type": self.invoice,
				"document_date": "2026-03-15",
				"file": upload.file_url,
			}
		).insert()
		self.assertTrue(frappe.get_list("Business Document", filters={"name": doc.name}))
		self.request(doc.file)
		self.assertTrue(response.download_private_file(doc.file))

	def test_outsider_has_no_access(self):
		doc = make_business_document(**party_fields(make_party()))
		frappe.set_user(make_outsider("archive-outsider@example.com"))
		with self.assertRaises(frappe.PermissionError):
			frappe.get_list("Business Document")
		with self.assertRaises(frappe.PermissionError):
			frappe.client.get("Business Document", doc.name)
		self.request(doc.file)
		with self.assertRaises(Forbidden):
			response.download_private_file(doc.file)

	def test_file_follows_document_permission(self):
		doc = make_business_document(**party_fields(make_party()))
		file = frappe.get_doc("File", {"file_url": doc.file, "attached_to_name": doc.name})
		self.assertTrue(file.has_permission("read", user=make_manager("archive-reader@example.com")))
		self.assertFalse(file.has_permission("read", user=make_outsider("archive-outsider2@example.com")))

	def test_read_bytes_returns_raw_pdf_bytes(self):
		local = make_file(PDF)
		data = content.read_bytes(local.file_url)
		self.assertIsInstance(data, bytes)
		self.assertEqual(data, PDF)
		self.assertIn(b"\x81\x9d", data)

	def test_read_bytes_refuses_paths_outside_files(self):
		self.assertIsNone(content.read_local("/private/files/../../site_config.json"))
		self.assertIsNone(content.read_local("/etc/passwd"))

	def test_read_file_hook_wins(self):
		with self.patch_hooks({"docarchive_read_file": ["docarchive.tests.factories.read_from_store"]}):
			self.assertEqual(
				content.read_bytes("/private/files/anything.pdf"), b"stored:/private/files/anything.pdf"
			)
