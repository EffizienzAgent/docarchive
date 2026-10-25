import base64
import email
import email.policy
import io
import json
import os
import tempfile
import time
import zipfile
from datetime import timedelta
from unittest.mock import patch

import frappe
from frappe.utils import now_datetime

from docarchive import audit, export, export_package, legal_hold, run
from docarchive.permissions import ADMIN_ROLE
from docarchive.tests.factories import (
	eml,
	make_document_type,
	make_manager,
	make_party,
	make_user,
	party_fields,
)
from docarchive.tests.hold_case import DOCUMENT, HoldTestCase
from docarchive.tests.test_attachments import attachment, xml_file

EXPORT = export.EXPORT
MAIL_FIELDS = ("thread_key", "mail_message_id", "mail_in_reply_to", "mail_references")
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

	def test_audit_entries_are_exported_with_links_both_ways(self):
		doc, other = self.archived(), self.archived()
		frappe.set_user("Administrator")
		legal_hold.set_legal_hold([doc.name], "x")
		frappe.db.after_commit.run()
		held = frappe.get_all(audit.LOG, filters={"action": audit.LEGAL_HOLD_SET}, pluck="name")[0]
		pending = audit.log("test.pending", documents=[(run.DOCUMENT, doc.name, "changed")])
		lost = audit.log("test.lost", documents=[(run.DOCUMENT, doc.name, "changed")], write=True)
		frappe.db.after_commit.run()
		frappe.db.delete("Archive Audit Copy", {"parent": lost, "target_name": "primary"})
		frappe.set_user(ADMIN)
		result = self.export(counterparty_type=doc.counterparty_type, counterparty=doc.counterparty)
		self.assertEqual(result.mismatches, 1)
		self.assertEqual(
			json.loads(result.mismatch_details)[0]["error"], "Not written to this storage target"
		)
		[archive] = self.zips(result)
		manifest = self.manifest(archive)
		rows = {row["id"]: row for row in manifest["documents"]}
		self.assertEqual(set(rows[doc.name]["audit"]), {held, pending, lost})
		self.assertEqual(rows[other.name]["audit"], [])
		entries = {entry["id"]: entry for entry in manifest["audit"]}
		self.assertEqual(set(entries), {held, pending, lost})
		self.assertEqual((entries[lost]["verified"], entries[lost]["pending"]), (False, False))
		self.assertEqual(entries[held]["documents"], [{"id": doc.name, "outcome": "held", "reason": None}])
		self.assertTrue(entries[held]["verified"])
		self.assertEqual((entries[pending]["verified"], entries[pending]["pending"]), (False, True))
		stored = self.fake("primary").body(
			"archive-primary", frappe.db.get_value(audit.LOG, held, "object_key")
		)
		self.assertEqual(archive.read(entries[held]["path"]), stored)
		self.assertTrue(entries[held]["path"].startswith("audit/"))
		self.assertIn("audit_tab", archive.read("index.html").decode())
		[completed] = frappe.get_all(audit.LOG, filters={"action": audit.EXPORT_COMPLETED}, pluck="name")
		outcomes = {r.document_name: r.outcome for r in frappe.get_doc(audit.LOG, completed).documents}
		self.assertEqual(outcomes, {doc.name: "exported", other.name: "exported"})

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


class TestMailThread(ExportTestCase):
	def test_thread_fields_come_from_the_archived_meta(self):
		first = self.archived_mail(eml(**{"Message-ID": "<one@example.test>"}))
		reply = self.archived_mail(
			eml(
				**{
					"Message-ID": "<two@example.test>",
					"In-Reply-To": "<one@example.test>",
					"References": "<one@example.test>",
				}
			)
		)
		self.assertTrue(first.object_key.endswith(".eml"))
		thread_key = json.loads(first.archive_meta)["correspondence"]["thread_key"]
		self.assertTrue(thread_key)
		for doc in (first, reply):
			frappe.db.set_value(DOCUMENT, doc.name, dict.fromkeys(MAIL_FIELDS))
		[archive] = self.zips(self.export())
		rows = {row["id"]: row for row in self.manifest(archive)["documents"]}
		self.assertEqual({rows[first.name]["thread_key"], rows[reply.name]["thread_key"]}, {thread_key})
		self.assertEqual(
			(rows[reply.name]["message_id"], rows[reply.name]["in_reply_to"], rows[reply.name]["references"]),
			("two@example.test", "one@example.test", ["one@example.test"]),
		)
		reply_at = json.loads(reply.archive_meta)["correspondence"]["at"]
		self.assertTrue(reply_at)
		self.assertEqual(rows[reply.name]["correspondence_at"], reply_at)
		self.assertTrue(rows[first.name]["files"][0]["preview"])

	def test_thread_fields_fall_back_to_the_document(self):
		doc = self.archived(
			thread_key="t-1", mail_message_id="a@x", mail_references="r1@x r2@x", archive_write=True
		)
		[archive] = self.zips(self.export())
		[row] = self.manifest(archive)["documents"]
		self.assertEqual(
			(row["thread_key"], row["message_id"], row["references"]), ("t-1", "a@x", ["r1@x", "r2@x"])
		)
		self.assertEqual(doc.name, row["id"])


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


PNG = base64.b64decode(
	"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


class TestMailRendering(ExportTestCase):
	def parse(self, raw):
		return email.message_from_bytes(raw, policy=email.policy.default)

	def test_inline_image_of_apple_mail_becomes_a_data_uri(self):
		raw = (
			b"From: Alice <alice@example.com>\r\nTo: bob@example.com\r\nSubject: Logo\r\n"
			b"MIME-Version: 1.0\r\nContent-Type: multipart/related; boundary=R\r\n\r\n"
			b"--R\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
			b'<p>Siehe Logo</p><img src="cid:logo@apple"><img src="https://t.example/p.gif">\r\n'
			b"--R\r\nContent-Type: image/png\r\nContent-Transfer-Encoding: base64\r\n"
			b"Content-ID: <logo@apple>\r\nContent-Disposition: inline; filename=logo.png\r\n\r\n"
			+ base64.b64encode(PNG)
			+ b"\r\n--R--\r\n"
		)
		body, attachments = export_package.render_mail(self.parse(raw))
		self.assertIn('src="data:image/png;base64,' + base64.b64encode(PNG).decode() + '"', body)
		self.assertNotIn("cid:logo@apple", body)
		self.assertIn("img-src data:", body)
		self.assertEqual(attachments, [])

	def test_attachment_is_extracted(self):
		raw = (
			b"From: a@example.com\r\nSubject: Rechnung\r\nMIME-Version: 1.0\r\n"
			b"Content-Type: multipart/mixed; boundary=M\r\n\r\n"
			b"--M\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nAnbei die Rechnung.\r\n"
			b"--M\r\nContent-Type: application/pdf\r\nContent-Transfer-Encoding: base64\r\n"
			b'Content-Disposition: attachment; filename="rechnung.pdf"\r\n\r\n'
			+ base64.b64encode(b"%PDF-1.7 test")
			+ b"\r\n--M--\r\n"
		)
		body, attachments = export_package.render_mail(self.parse(raw))
		self.assertIn("Anbei die Rechnung.", body)
		self.assertEqual(attachments, [("rechnung.pdf", b"%PDF-1.7 test")])

	def test_transfer_encodings_and_encoded_headers(self):
		raw = (
			b"From: =?utf-8?q?J=C3=BCrgen_M=C3=BCller?= <j@example.com>\r\n"
			b"Subject: =?utf-8?b?w4RuZGVydW5nIGRlciBSZWNobnVuZw==?=\r\nMIME-Version: 1.0\r\n"
			b"Content-Type: multipart/alternative; boundary=A\r\n\r\n"
			b"--A\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Transfer-Encoding: quoted-printable\r\n\r\n"
			b"Gr=C3=BC=C3=9Fe\r\n"
			b"--A\r\nContent-Type: text/html; charset=utf-8\r\nContent-Transfer-Encoding: base64\r\n\r\n"
			+ base64.b64encode("<p>Grüße aus Köln</p>".encode())
			+ b"\r\n--A--\r\n"
		)
		body, _ = export_package.render_mail(self.parse(raw))
		self.assertIn("Grüße aus Köln", body)
		self.assertIn("Jürgen Müller", body)
		self.assertIn("Änderung der Rechnung", body)

	def test_plain_text_only_is_escaped_and_labels_always_show(self):
		raw = b"From: a@example.com\r\nSubject: Hinweis\r\n\r\n<b>kein HTML</b>\r\nZeile 2"
		body, attachments = export_package.render_mail(self.parse(raw))
		self.assertIn("&lt;b&gt;kein HTML&lt;/b&gt;", body)
		self.assertIn("<pre>", body)
		self.assertIn("white-space:pre-wrap", body)
		for label in ("Subject", "From", "To", "Cc", "Date"):
			self.assertIn(f"<th>{label}</th>", body)
		self.assertEqual(attachments, [])

	def test_package_lists_mail_attachments_for_the_viewer(self):
		doc = self.archived()
		raw = (
			b"From: a@example.com\r\nSubject: Hi\r\nMIME-Version: 1.0\r\n"
			b"Content-Type: multipart/mixed; boundary=M\r\n\r\n"
			b"--M\r\nContent-Type: text/plain\r\n\r\nBody\r\n"
			b'--M\r\nContent-Type: text/plain\r\nContent-Disposition: attachment; filename="../../evil name.txt"\r\n\r\n'
			b"data\r\n--M--\r\n"
		)
		path = frappe.get_site_path("private", "files", "export-attachment.eml")
		with open(path, "wb") as f:
			f.write(raw)
		buffer = io.BytesIO()
		package = export_package.Package(buffer, {"export": "x"})
		item = {"key": doc.object_key.rsplit(".", 1)[0] + ".eml", "version_id": "v", "sha256": None}
		package.pending[doc.name] = {"objects": {}, "metas": {}, "previews": {}}
		package.add_object(doc, item, {"sha256": "s", "stored_sha256": "s", "size": len(raw), "path": path})
		entry = package.pending[doc.name]
		[listed] = entry["mail_attachments"][item["key"]]
		self.assertEqual((listed["name"], listed["size"]), ("evil name.txt", 4))
		self.assertTrue(listed["download_script"].endswith("/1-evil name.txt.js"))
		package.close()
		script = zipfile.ZipFile(buffer).read(listed["download_script"]).decode()
		self.assertEqual(
			script, f'window.docarchiveDownload("evil name.txt", "{base64.b64encode(b"data").decode()}");\n'
		)


class TestPaths(ExportTestCase):
	def test_old_scheme_documents_get_the_new_export_names(self):
		doc = frappe._dict(
			name="DOC-1",
			object_key="acme/2026/invoice/org-1/2026-03-15_inv-1_doc-1.pdf",
			archive_meta=json.dumps({"document": {"id": "DOC-1", "date": "2026-03-15", "number": "INV 1"}}),
		)
		base = "acme/2026/invoice/org-1/DOC-1_2026-03-15_INV-1"
		for old, new in (
			(doc.object_key, f"{base}.pdf"),
			("acme/2026/invoice/org-1/2026-03-15_inv-1_doc-1__att-1-x.xml", f"{base}__att-1-x.xml"),
			(doc.object_key + ".meta.json", f"{base}.pdf.meta.json"),
			(doc.object_key + ".meta.2.json", f"{base}.pdf.meta.2.json"),
			("other/key.pdf", "other/key.pdf"),
		):
			self.assertEqual(export_package.export_key(doc, old), new)

	def test_eml_gets_a_download_script(self):
		doc = self.archived()
		raw = b"From: a@example.com\r\nSubject: Hi\r\n\r\nBody"
		path = frappe.get_site_path("private", "files", "export-download.eml")
		with open(path, "wb") as f:
			f.write(raw)
		buffer = io.BytesIO()
		package = export_package.Package(buffer, {"export": "x"})
		item = {"key": "a/mail.eml", "version_id": "v", "sha256": None}
		package.add_object(doc, item, {"sha256": "s", "stored_sha256": "s", "size": len(raw), "path": path})
		script = package.pending[doc.name]["downloads"]["a/mail.eml"]
		package.close()
		self.assertEqual(script, "previews/a/mail.eml.download.js")
		body = zipfile.ZipFile(buffer).read(script).decode()
		self.assertIn(base64.b64encode(raw).decode(), body)
		self.assertTrue(body.startswith('window.docarchiveDownload("mail.eml", "'))

	def test_archive_paths_stay_inside_the_package(self):
		self.assertEqual(export_package.archive_path("../../etc/passwd"), "documents/etc/passwd")
		self.assertEqual(export_package.preview_path("a/b.eml"), "previews/a/b.eml.html")


class TestHardening(ExportTestCase):
	def test_viewer_data_cannot_break_out_of_the_script(self):
		doc = self.archived(title="<!--<script \u2028 & </script>")
		result = self.export()
		self.assertEqual((result.status, result.error), ("Completed", None))
		[archive] = self.zips(result)
		viewer = archive.read("index.html").decode()
		payload = viewer.split("const DATA = ", 1)[1].split(";\n", 1)[0]
		for char in ("<", ">", "&", "\u2028"):
			self.assertNotIn(char, payload)
		self.assertIn("<!--<script", doc.title)
		self.assertEqual(json.loads(payload)["documents"][0]["title"], doc.title)

	def test_only_one_export_at_a_time(self):
		export.start_export({})
		with self.assertRaises(frappe.ValidationError):
			export.start_export({})

	def test_part_size_is_capped(self):
		frappe.db.set_single_value("Archive Settings", "export_part_size_mb", 999999)
		self.assertEqual(export.part_limit(), export.MAX_PART_MB * 1024 * 1024)

	def test_stale_exports_and_temp_dirs_are_cleaned_up(self):
		name = export.start_export({})
		frappe.db.set_value(EXPORT, name, "status", "Running")
		old = now_datetime() - timedelta(hours=export.STALE_HOURS + 1)
		frappe.db.set_value(EXPORT, name, "modified", old, update_modified=False)
		stale_dir = tempfile.mkdtemp(prefix=export.TEMP_PREFIX)
		fresh_dir = tempfile.mkdtemp(prefix=export.TEMP_PREFIX)
		past = time.time() - (export.STALE_HOURS + 1) * 3600
		os.utime(stale_dir, (past, past))
		export.expire()
		self.assertEqual(frappe.db.get_value(EXPORT, name, "status"), "Failed")
		self.assertFalse(os.path.exists(stale_dir))
		self.assertTrue(os.path.exists(fresh_dir))
		os.rmdir(fresh_dir)

	def test_failing_cleanup_still_marks_the_export_failed(self):
		self.archived()
		with (
			patch("docarchive.export.build_part", side_effect=RuntimeError("boom")),
			patch("docarchive.export.delete_files", side_effect=RuntimeError("gone")),
		):
			result = self.export()
		self.assertEqual(result.status, "Failed")

	def test_export_files_need_the_archive_admin_role(self):
		self.archived()
		result = self.export()
		file = frappe.get_doc("File", {"file_url": result.parts[0].file})
		self.assertTrue(frappe.has_permission("File", "read", file, user=ADMIN))
		frappe.db.set_value("File", file.name, "owner", "export-manager@example.com")
		manager = make_manager("export-manager@example.com")
		file.reload()
		self.assertFalse(frappe.has_permission("File", "read", file, user=manager))

	def test_user_permissions_limit_the_export(self):
		other_type = make_document_type(default_retention_years=8)
		allowed = self.archived()
		self.archived(document_type=other_type.name)
		frappe.set_user("Administrator")
		frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": ADMIN,
				"allow": "Business Document Type",
				"for_value": allowed.document_type,
			}
		).insert(ignore_permissions=True)
		frappe.set_user(ADMIN)
		try:
			[archive] = self.zips(self.export())
			self.assertEqual([row["id"] for row in self.manifest(archive)["documents"]], [allowed.name])
		finally:
			frappe.set_user("Administrator")
			for name in frappe.get_all("User Permission", filters={"user": ADMIN}, pluck="name"):
				frappe.delete_doc("User Permission", name, ignore_permissions=True)
