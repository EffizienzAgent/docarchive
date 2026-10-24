import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import now_datetime

from docarchive import content, guards
from docarchive.tests.factories import (
	make_business_document,
	make_file,
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


class TestSharedObjectGuards(IntegrationTestCase):
	def setUp(self):
		make_settings(default_own_company=make_own_company().name)
		self.party = make_party()
		self.doc = make_business_document(**party_fields(self.party))
		self.file = attached_file(self.doc)
		self.data = content.read_bytes(self.doc.file)
		frappe.db.set_value("Business Document", self.doc.name, "archive_started_at", now_datetime())

	def twin(self):
		return make_file(
			self.data, file_name="twin.pdf", attached_to_doctype="Contact", attached_to_name=self.party.name
		)

	def test_twin_shares_the_archived_object(self):
		twin = self.twin()
		self.assertEqual((twin.file_url, twin.content_hash), (self.file.file_url, self.file.content_hash))

	def test_twin_object_fields_cannot_change(self):
		for field, value in (
			("is_private", 0),
			("content_hash", "0" * 32),
			("thumbnail_url", "/private/files/other.png"),
			("file_url", "/private/files/other.pdf"),
		):
			with self.subTest(field=field):
				twin = self.twin()
				twin.set(field, value)
				with self.assertRaises(frappe.ValidationError):
					twin.save(ignore_permissions=True)
		self.assertEqual(content.read_bytes(self.doc.file), self.data)

	def test_twin_delete_keeps_bytes(self):
		self.twin().delete(ignore_permissions=True)
		self.assertEqual(content.read_bytes(self.doc.file), self.data)

	def test_new_file_cannot_point_its_thumbnail_at_the_archive(self):
		with self.assertRaises(frappe.ValidationError):
			make_file(thumbnail_url=self.doc.file)

	def test_new_file_on_the_archived_url_gets_the_real_hash(self):
		claimed = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "claimed.pdf",
				"file_url": self.doc.file,
				"content_hash": "0" * 32,
				"is_private": 1,
			}
		).insert(ignore_permissions=True)
		self.assertEqual(claimed.content_hash, self.file.content_hash)
		claimed.delete(ignore_permissions=True)
		self.assertEqual(content.read_bytes(self.doc.file), self.data)

	def test_row_with_archived_thumbnail_cannot_be_deleted(self):
		other = make_file()
		frappe.db.set_value("File", other.name, "thumbnail_url", self.doc.file)
		with self.assertRaises(frappe.ValidationError):
			guards.protect_file(frappe.get_doc("File", other.name))

	def test_object_hook_refuses_archived_url_and_key(self):
		with self.assertRaises(frappe.ValidationError):
			guards.protect_object(self.doc.file)
		key = "private/files/" + self.doc.file.rsplit("/", 1)[-1]
		with self.assertRaises(frappe.ValidationError):
			guards.protect_object(key=key)
		guards.protect_object(make_file().file_url)
		guards.protect_object(self.doc.file.upper())

	def test_object_hooks_as_s3storage_calls_them(self):
		key = "private/files/" + self.doc.file.rsplit("/", 1)[-1]
		for hook in ("s3storage_before_object_delete", "s3storage_before_object_write"):
			methods = frappe.get_hooks(hook)
			self.assertIn("docarchive.guards.protect_object", methods, hook)
			with self.subTest(hook=hook), self.assertRaisesRegex(frappe.ValidationError, "cannot be changed"):
				for method in methods:
					frappe.get_attr(method)(file_url=self.doc.file, key=key)
			fresh = make_file()
			for method in methods:
				frappe.get_attr(method)(file_url=fresh.file_url, key="private/files/" + fresh.file_name)
		self.assertFalse(frappe.get_hooks("s3storage_before_object_change"))
