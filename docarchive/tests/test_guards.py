import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import now_datetime

from docarchive import content
from docarchive.tests.factories import (
	make_business_document,
	make_manager,
	make_own_company,
	make_party,
	make_settings,
	party_fields,
)


def attached_file(doc):
	return frappe.get_doc("File", {"attached_to_doctype": "Business Document", "attached_to_name": doc.name})


def run_before_delete_hooks(file_doc):
	for method in frappe.get_hooks("s3storage_before_file_delete"):
		frappe.get_attr(method)(file_doc)


class TestFileGuards(IntegrationTestCase):
	def setUp(self):
		make_settings(default_own_company=make_own_company().name)
		self.doc = make_business_document(**party_fields(make_party()))
		self.file = attached_file(self.doc)
		self.data = content.read_bytes(self.doc.file)
		frappe.db.set_value("Business Document", self.doc.name, "archive_started_at", now_datetime())

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_storage_hook_refuses_archived_file(self):
		self.assertIn("docarchive.guards.protect_file", frappe.get_hooks("s3storage_before_file_delete"))
		with self.assertRaises(frappe.ValidationError):
			run_before_delete_hooks(self.file)
		self.assertEqual(content.read_bytes(self.doc.file), self.data)

	def test_storage_hook_allows_files_of_open_documents(self):
		loose = make_business_document()
		run_before_delete_hooks(attached_file(loose))

	def test_delete_by_user_refused_and_bytes_kept(self):
		loose = attached_file(make_business_document())
		frappe.set_user(make_manager("guard-delete@example.com"))
		self.assertFalse(self.file.has_permission("delete"))
		self.assertTrue(loose.has_permission("delete"))
		with self.assertRaises(frappe.PermissionError):
			frappe.delete_doc("File", self.file.name)
		frappe.set_user("Administrator")
		self.assertTrue(frappe.db.exists("File", self.file.name))
		self.assertEqual(content.read_bytes(self.doc.file), self.data)

	def test_file_cannot_be_moved_or_detached(self):
		for field, value in (
			("is_private", 0),
			("attached_to_name", make_business_document().name),
			("attached_to_doctype", None),
		):
			with self.subTest(field=field):
				file = frappe.get_doc("File", self.file.name)
				file.set(field, value)
				with self.assertRaises(frappe.ValidationError):
					file.save(ignore_permissions=True)
		self.assertEqual(content.read_bytes(self.doc.file), self.data)

	def test_open_document_file_can_change(self):
		loose = attached_file(make_business_document())
		loose.is_private = 0
		loose.save(ignore_permissions=True)

	def test_other_file_edits_allowed(self):
		file = frappe.get_doc("File", self.file.name)
		file.file_name = "renamed.pdf"
		file.save(ignore_permissions=True)
