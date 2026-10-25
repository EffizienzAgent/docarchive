import email
import email.policy
import io
import json
import zipfile
from datetime import timedelta
from unittest.mock import patch

import frappe
from frappe.utils import now_datetime

from docarchive import export, export_package, run
from docarchive.permissions import ADMIN_ROLE
from docarchive.tests.factories import make_manager, make_party, make_user, party_fields
from docarchive.tests.hold_case import HoldTestCase
from docarchive.tests.test_attachments import attachment, xml_file

EXPORT = export.EXPORT
ADMIN = "export-admin@example.com"


class ExportTestCase(HoldTestCase):
	def setUp(self):
		super().setUp()
		frappe.db.delete(EXPORT)
		self.queued = []
		self.enterContext(patch("frappe.enqueue", side_effect=lambda *a, **kw: self.queued.append(kw)))
		self.enterContext(patch("frappe.db.rollback"))
		frappe.set_user(make_user(ADMIN, [ADMIN_ROLE]))

	def tearDown(self):
		frappe.set_user("Administrator")
		super().tearDown()

	def export(self, **scope):
		name = export.start_export(scope)
		[job] = [job for job in self.queued if job.get("job_id", "").startswith("docarchive-export-")]
		self.queued.clear()
		self.assertEqual(job["queue"], "long")
		export.run_export(job["name"])
		return frappe.get_doc(EXPORT, name)

	def zips(self, doc):
		result = []
		for part in doc.parts:
			file = frappe.get_doc("File", {"file_url": part.file})
			self.assertTrue(file.is_private)
			result.append(zipfile.ZipFile(io.BytesIO(file.get_content())))
		return result

	def manifest(self, archive):
		return json.loads(archive.read("manifest.json"))


class TestStartExport(ExportTestCase):
	def test_only_archive_admins_may_export(self):
		frappe.set_user(make_manager("export-manager@example.com"))
		self.assertFalse(export.access()["export"])
		with self.assertRaises(frappe.PermissionError):
			export.start_export({})
		self.assertFalse(frappe.db.exists(EXPORT, {}))

	def test_scope_is_validated(self):
		with self.assertRaises(frappe.ValidationError):
			export.start_export({"date_from": "2026-02-01", "date_to": "2026-01-01"})
		with self.assertRaises(frappe.ValidationError):
			export.start_export({"counterparty": "x"})
		with self.assertRaises(frappe.ValidationError):
			export.start_export({"target_name": "nowhere"})

	def test_defaults_to_the_first_target_and_logs_the_request(self):
		self.assertEqual(export.access(), {"export": True, "targets": ["primary", "secondary"]})
		doc = frappe.get_doc(EXPORT, export.start_export({}))
		self.assertEqual((doc.target_name, doc.requested_by, doc.status), ("primary", ADMIN, "Queued"))


class TestExportPackage(ExportTestCase):
	def test_exports_files_metadata_manifest_and_viewer(self):
		doc = self.archived(
			attachments=[attachment(xml_file().file_url, role="Structured Invoice (XML)")],
			title="</script><script>alert(1)</script>",
		)
		result = self.export()
		self.assertEqual(result.status, "Completed")
		self.assertEqual((result.total_documents, result.exported_documents, result.mismatches), (1, 1, 0))
		self.assertTrue(result.expires_at)
		[archive] = self.zips(result)
		names = set(archive.namelist())
		self.assertTrue({"index.html", "manifest.json", "manifest.csv"} <= names)
		[row] = self.manifest(archive)["documents"]
		self.assertEqual(row["id"], doc.name)
		self.assertTrue(row["verified"])
		self.assertEqual([file["role"] for file in row["files"]], ["Main", "Structured Invoice (XML)"])
		stored = frappe.get_doc("File", {"file_url": doc.file}).get_content()
		self.assertEqual(archive.read(row["files"][0]["path"]), stored)
		self.assertEqual(archive.getinfo(row["files"][0]["path"]).compress_type, zipfile.ZIP_STORED)
		self.assertEqual([meta["key"] for meta in row["metadata"]], [doc.meta_key])
		self.assertIn(row["files"][0]["key"], archive.read("manifest.csv").decode())
		viewer = archive.read("index.html").decode()
		self.assertNotIn("</script><script>alert(1)", viewer)
		self.assertIn(doc.name, viewer)
		self.assertNotIn(export_package.DATA_MARKER, viewer)

	def test_filters_by_counterparty(self):
		mine = self.archived()
		other = make_party("Other")
		self.archived(**party_fields(other))
		result = self.export(counterparty_type=mine.counterparty_type, counterparty=mine.counterparty)
		[archive] = self.zips(result)
		self.assertEqual([row["id"] for row in self.manifest(archive)["documents"]], [mine.name])
		self.assertEqual(self.manifest(archive)["scope"]["filters"]["counterparty"], mine.counterparty)

	def test_mismatch_is_reported_without_aborting(self):
		doc = self.archived()
		self.fake("primary").versions[("archive-primary", doc.object_key)][0]["Body"] = b"tampered"
		result = self.export()
		self.assertEqual((result.status, result.mismatches), ("Completed", 1))
		self.assertEqual(json.loads(result.mismatch_details)[0]["key"], doc.object_key)
		[archive] = self.zips(result)
		[row] = self.manifest(archive)["documents"]
		self.assertFalse(row["verified"])
		self.assertFalse(row["files"][0]["verified"])

	def test_unreadable_object_is_reported(self):
		doc = self.archived()
		with patch("docarchive.store.download", side_effect=RuntimeError("down")):
			result = self.export()
		self.assertEqual(result.status, "Completed")
		self.assertEqual(result.mismatches, 2)
		self.assertEqual(
			{row["key"] for row in json.loads(result.mismatch_details)}, {doc.object_key, doc.meta_key}
		)

	def test_documents_missing_on_the_target_are_skipped(self):
		doc = self.archived()
		self.archived()
		copy = next(c for c in doc.archive_copies if c.target_name == "secondary")
		frappe.db.set_value("Archive Copy", copy.name, "status", "Failed")
		result = self.export(target_name="secondary")
		self.assertEqual((result.total_documents, result.skipped_documents), (1, 1))

	def test_large_exports_are_split_into_parts(self):
		docs = [self.archived() for _ in range(3)]
		with patch("docarchive.export.part_limit", return_value=export.planned_size(docs[0]) + 1):
			result = self.export()
		self.assertEqual([part.part for part in result.parts], [1, 2, 3])
		archives = self.zips(result)
		ids = [row["id"] for archive in archives for row in self.manifest(archive)["documents"]]
		self.assertEqual(sorted(ids), sorted(doc.name for doc in docs))
		self.assertEqual(self.manifest(archives[1])["scope"]["part"], 2)
		self.assertTrue(all("index.html" in archive.namelist() for archive in archives))

	def test_retention_trail_from_sidecars(self):
		self.type = frappe.get_doc("Business Document Type", self.type.name)
		self.type.retention_from = "Contract End Date"
		self.type.save(ignore_permissions=True)
		doc = self.archived(contract_end_date="2026-12-31")
		doc.contract_end_date = "2030-06-30"
		doc.save(ignore_permissions=True)
		self.assertEqual(run.nightly()["extended"], 1)
		[archive] = self.zips(self.export())
		[row] = self.manifest(archive)["documents"]
		self.assertEqual(len(row["history"]), 1)
		self.assertEqual(row["history"][0]["contract_end_date"], "2030-06-30")
		self.assertEqual(len(row["metadata"]), 2)
		self.assertTrue(row["verified"])


class TestExpiry(ExportTestCase):
	def test_expired_exports_lose_their_files(self):
		self.archived()
		result = self.export()
		file_url = result.parts[0].file
		frappe.db.set_value(EXPORT, result.name, "expires_at", now_datetime() - timedelta(days=1))
		export.expire()
		result.reload()
		self.assertEqual(result.status, "Expired")
		self.assertFalse(result.parts[0].file)
		self.assertFalse(frappe.db.exists("File", {"file_url": file_url}))


class TestMailPreview(ExportTestCase):
	def message(self, html_body):
		raw = (
			"From: Alice <alice@example.com>\r\nTo: bob@example.com\r\nSubject: Angebot\r\n"
			"MIME-Version: 1.0\r\nContent-Type: text/html; charset=utf-8\r\n\r\n" + html_body
		)
		return email.message_from_bytes(raw.encode(), policy=email.policy.default)

	def test_preview_is_sanitized_and_blocks_remote_content(self):
		preview = export_package.mail_preview(
			self.message(
				'<p onclick="x()">Hallo</p><script>alert(1)</script><img src="https://t.example/p.gif">'
			)
		)
		self.assertIn("Hallo", preview)
		self.assertIn("Angebot", preview)
		self.assertNotIn("<script>alert", preview)
		self.assertNotIn("onclick", preview)
		self.assertIn("default-src 'none'", preview)

	def test_eml_objects_get_a_preview_next_to_the_original(self):
		doc = self.archived()
		raw = b"From: a@example.com\r\nSubject: Hi\r\n\r\nPlain body"
		path = frappe.get_site_path("private", "files", "export-test.eml")
		with open(path, "wb") as f:
			f.write(raw)
		buffer = io.BytesIO()
		package = export_package.Package(buffer, {"export": "x"})
		item = {"key": "a/mail.eml", "version_id": "v", "sha256": None}
		package.add_object(doc, item, {"sha256": "s", "stored_sha256": "s", "size": len(raw), "path": path})
		package.close()
		archive = zipfile.ZipFile(buffer)
		self.assertEqual(archive.read("documents/a/mail.eml"), raw)
		self.assertIn("Plain body", archive.read("previews/a/mail.eml.html").decode())


class TestPaths(ExportTestCase):
	def test_archive_paths_stay_inside_the_package(self):
		self.assertEqual(export_package.archive_path("../../etc/passwd"), "documents/etc/passwd")
		self.assertEqual(export_package.preview_path("a/b.eml"), "previews/a/b.eml.html")
