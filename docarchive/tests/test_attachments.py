import base64
import hashlib
import io
import json
import re
from datetime import UTC, datetime
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from werkzeug.datastructures import MultiDict
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from docarchive import api, content, documents, guards, run, store
from docarchive.document_archive.doctype.business_document.test_business_document import start_archive
from docarchive.permissions import INTEGRATION_ROLE
from docarchive.tests.factories import (
	make_business_document,
	make_document_type,
	make_file,
	make_manager,
	make_own_company,
	make_party,
	make_settings,
	make_user,
	party_fields,
	pdf,
	unique,
)
from docarchive.tests.test_run import DOCUMENT, ArchiveRunTestCase

XML = b'<?xml version="1.0"?><Invoice/>'


def attachment(file=None, **fields):
	return {"file": file or make_file().file_url, **fields}


def xml_file(**fields):
	return make_file(XML + unique("x").encode(), file_name=f"{unique('invoice')}.xml", **fields)


class TestAttachments(IntegrationTestCase):
	def setUp(self):
		make_settings(default_own_company=make_own_company().name)

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_rows_get_checksum_title_and_their_file(self):
		upload = make_file(file_name="Leistungsnachweis_Maerz.pdf")
		doc = make_business_document(attachments=[attachment(upload.file_url, role="Proof of Service")])
		[row] = doc.attachments
		self.assertEqual(row.content_sha256, content.sha256(content.read_bytes(upload.file_url)))
		self.assertEqual((row.role, row.title), ("Proof of Service", "Leistungsnachweis Maerz"))
		self.assertEqual(
			frappe.db.get_value("File", upload.name, ["attached_to_doctype", "attached_to_name"]),
			(DOCUMENT, doc.name),
		)

	def test_same_guards_as_the_main_file(self):
		party = make_party()
		frappe.set_user(make_manager("attachment-owner@example.com"))
		foreign = make_file()
		frappe.set_user(make_manager("attachment-other@example.com"))
		cases = {
			"public": make_file(is_private=0).file_url,
			"other user": foreign.file_url,
			"other record": make_file(attached_to_doctype="Contact", attached_to_name=party.name).file_url,
			"file type": make_file(file_name="page.html").file_url,
			"case": make_file(file_name=f"{unique('Case')}.pdf").file_url.lower(),
		}
		for case, file_url in cases.items():
			with self.subTest(case), self.assertRaises(frappe.ValidationError):
				make_business_document(attachments=[attachment(file_url)])

	def test_same_file_twice_rejected(self):
		upload = make_file()
		with self.assertRaises(frappe.ValidationError):
			make_business_document(file=upload.file_url, attachments=[attachment(upload.file_url)])
		data = pdf(b"twice")
		with self.assertRaises(frappe.ValidationError):
			make_business_document(
				attachments=[attachment(make_file(data).file_url), attachment(make_file(data).file_url)]
			)

	def test_deduplicated_upload_of_another_user(self):
		data = XML + b"shared"
		party = make_party()
		elsewhere = make_file(
			data, file_name="mail.xml", attached_to_doctype="Contact", attached_to_name=party.name
		)
		frappe.set_user(make_manager("attachment-dedupe@example.com"))
		upload = make_file(data, file_name="Rechnung.xml")
		self.assertEqual(upload.file_url, elsewhere.file_url)
		doc = make_business_document(attachments=[attachment(upload.file_url)])
		frappe.set_user("Administrator")
		self.assertEqual(doc.attachments[0].title, "Rechnung")
		self.assertEqual(frappe.db.get_value("File", elsewhere.name, "attached_to_doctype"), "Contact")
		self.assertEqual(frappe.db.get_value("File", upload.name, "attached_to_name"), doc.name)

	def test_checksum_is_server_only(self):
		doc = make_business_document(attachments=[attachment()])
		real = doc.attachments[0].content_sha256
		doc.attachments[0].content_sha256 = "0" * 64
		doc.attachments[0].object_key = "elsewhere"
		doc.save(ignore_permissions=True)
		doc.reload()
		self.assertEqual((doc.attachments[0].content_sha256, doc.attachments[0].object_key), (real, None))

	def test_rows_can_change_before_archiving(self):
		doc = make_business_document(attachments=[attachment()])
		doc.attachments[0].file = xml_file().file_url
		doc.append("attachments", attachment())
		doc.save(ignore_permissions=True)
		doc.remove(doc.attachments[1])
		doc.save(ignore_permissions=True)
		self.assertEqual(len(doc.reload().attachments), 1)

	def test_attachment_list_is_locked_after_archive_start(self):
		doc = start_archive(make_business_document(attachments=[attachment(), attachment()]))
		changes = {
			"add": lambda d: d.append("attachments", attachment()),
			"remove": lambda d: d.remove(d.attachments[0]),
			"file": lambda d: d.attachments[0].update({"file": make_file().file_url}),
			"role": lambda d: d.attachments[0].update({"role": "Proof of Service"}),
			"title": lambda d: d.attachments[0].update({"title": "Other"}),
			"order": lambda d: d.set("attachments", list(reversed(d.attachments))),
		}
		for case, change in changes.items():
			with self.subTest(case):
				doc.reload()
				change(doc)
				with self.assertRaises(frappe.ValidationError):
					doc.save(ignore_permissions=True)
		doc.reload()
		doc.title = "Still editable"
		doc.save(ignore_permissions=True)

	def test_rows_cannot_be_saved_or_deleted_directly_after_archive_start(self):
		doc = start_archive(make_business_document(attachments=[attachment()]))
		row = frappe.get_doc("Business Document Attachment", doc.attachments[0].name)
		row.role = "Proof of Service"
		with self.assertRaises(frappe.ValidationError):
			row.save(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			row.delete(ignore_permissions=True)
		new = frappe.get_doc(
			{
				"doctype": "Business Document Attachment",
				"parenttype": DOCUMENT,
				"parent": doc.name,
				"parentfield": "attachments",
				**attachment(),
			}
		)
		with self.assertRaises(frappe.ValidationError):
			new.insert(ignore_permissions=True)
		self.assertEqual(len(doc.reload().attachments), 1)

	def test_attachment_files_are_guarded_after_archive_start(self):
		doc = start_archive(make_business_document(attachments=[attachment()]))
		file = frappe.get_doc("File", {"file_url": doc.attachments[0].file, "attached_to_name": doc.name})
		with self.assertRaises(frappe.ValidationError):
			guards.protect_file(file)
		with self.assertRaises(frappe.ValidationError):
			guards.protect_object(file_url=file.file_url)
		for field, value in (("is_private", 0), ("attached_to_name", None)):
			with self.subTest(field):
				file.reload()
				file.set(field, value)
				with self.assertRaises(frappe.ValidationError):
					file.save(ignore_permissions=True)
		frappe.set_user(make_manager("attachment-delete@example.com"))
		with self.assertRaises(frappe.PermissionError):
			frappe.delete_doc("File", file.name)

	def test_upload_dialog_can_combine_files(self):
		party = make_party()
		urls = [make_file().file_url, xml_file().file_url, make_file().file_url]
		[name] = documents.create_documents(
			party.doctype, party.name, json.dumps(urls), make_document_type().name, "2026-03-15", combine=1
		)
		doc = frappe.get_doc(DOCUMENT, name)
		self.assertEqual((doc.file, [row.file for row in doc.attachments]), (urls[0], urls[1:]))
		separate = documents.create_documents(
			party.doctype,
			party.name,
			json.dumps([make_file().file_url, make_file().file_url]),
			make_document_type().name,
			"2026-03-15",
		)
		self.assertEqual(len(separate), 2)


class TestArchiveAttachments(ArchiveRunTestCase):
	def archive(self, doc):
		return run.archive_document(doc.name, store.targets())

	def with_attachments(self, **fields):
		return self.document(
			attachments=[
				attachment(xml_file().file_url, role="Structured Invoice (XML)"),
				attachment(make_file(file_name="Stundenzettel März.pdf").file_url, role="Proof of Service"),
			],
			**fields,
		)

	def test_every_attachment_is_locked_on_every_target(self):
		doc = self.with_attachments()
		self.assertEqual(self.archive(doc), "Archived")
		doc.reload()
		base = doc.object_key.removesuffix(".pdf")
		self.assertRegex(doc.attachments[0].object_key, rf"^{re.escape(base)}__att-1-invoice-[0-9a-f]+\.xml$")
		self.assertRegex(
			doc.attachments[1].object_key, rf"^{re.escape(base)}__att-2-stundenzettel-maerz[0-9a-f]*\.pdf$"
		)
		puts = self.puts()
		self.assertEqual(len(puts), 4 * 2)
		keys = [doc.object_key, *(row.object_key for row in doc.attachments), doc.meta_key]
		self.assertEqual(
			sorted((p["Bucket"], p["Key"]) for p in puts),
			sorted((f"archive-{n}", k) for n in ("primary", "secondary") for k in keys),
		)
		for put in puts:
			self.assertEqual(put["ObjectLockMode"], "GOVERNANCE")
			self.assertEqual(put["ObjectLockRetainUntilDate"], datetime(2035, 1, 2, tzinfo=UTC))
			self.assertTrue(put["ContentMD5"])
			self.assertEqual(put["Metadata"]["sha256"], hashlib.sha256(put["Body"]).hexdigest())
		self.assertEqual(
			{p["ContentType"] for p in puts if p["Key"] == doc.attachments[0].object_key}, {"application/xml"}
		)
		self.assertEqual(len(doc.attachment_copies), 4)
		for row in doc.attachment_copies:
			fake = self.fake(row.target_name)
			self.assertEqual(
				fake.latest(f"archive-{row.target_name}", row.object_key)["VersionId"], row.version_id
			)
			reads = {
				kw["VersionId"] for op, kw in fake.calls if op == "get_object" and kw["Key"] == row.object_key
			}
			self.assertEqual(reads, {row.version_id})

	def test_meta_lists_every_file(self):
		doc = self.with_attachments()
		self.archive(doc)
		doc.reload()
		meta = json.loads(self.fake("primary").body("archive-primary", doc.meta_key))
		self.assertEqual(
			[(f["key"], f["role"], f["sha256"]) for f in meta["files"]],
			[
				(doc.object_key, "Main", doc.archive_sha256),
				*((row.object_key, row.role, row.content_sha256) for row in doc.attachments),
			],
		)
		self.assertTrue(meta["files"][2]["name"].startswith("Stundenzettel März"))
		self.assertEqual(meta["files"][2]["size"], len(content.read_bytes(doc.attachments[1].file)))
		self.assertEqual(meta["file"], meta["files"][0])
		for entry in meta["files"]:
			body = self.fake("secondary").body("archive-secondary", entry["key"])
			self.assertEqual(hashlib.sha256(body).hexdigest(), entry["sha256"])

	def test_document_without_attachments_is_unchanged(self):
		doc = self.document()
		self.archive(doc)
		doc.reload()
		meta = json.loads(self.fake("primary").body("archive-primary", doc.meta_key))
		self.assertEqual(meta["files"], [meta["file"]])
		self.assertEqual((len(self.puts()), doc.attachment_copies), (4, []))

	def test_partial_target_retry_writes_only_what_is_missing(self):
		doc = self.with_attachments()
		failing = {"secondary"}
		original = store.put_locked

		def flaky(target, key, *args, **kwargs):
			if target.name in failing and "__att-2-" in key:
				raise store.LockError("boom")
			return original(target, key, *args, **kwargs)

		with patch("docarchive.store.put_locked", side_effect=flaky):
			self.assertEqual(self.archive(doc), "Failed")
		doc.reload()
		self.assertEqual([c.status for c in doc.archive_copies], ["Archived", "Failed"])
		self.assertEqual(
			sorted(
				(r.target_name, r.object_key.rsplit("__", 1)[-1][:5], bool(r.version_id))
				for r in doc.attachment_copies
			),
			[
				("primary", "att-1", True),
				("primary", "att-2", True),
				("secondary", "att-1", True),
				("secondary", "att-2", False),
			],
		)
		self.assertEqual(len(self.fake("primary").puts()), 4)
		self.assertEqual(len(self.fake("secondary").puts()), 2)
		failing.clear()
		self.assertEqual(self.archive(doc), "Archived")
		doc.reload()
		self.assertEqual(len(self.fake("primary").puts()), 4)
		self.assertEqual(
			[p["Key"] for p in self.fake("secondary").puts()][2:],
			[doc.attachments[1].object_key, doc.meta_key],
		)
		self.assertTrue(all(len(v) == 1 for v in self.fake("secondary").versions.values()))
		self.assertEqual(len(doc.attachment_copies), 4)
		self.assertTrue(all(row.version_id for row in doc.attachment_copies))

	def test_changed_attachment_bytes_fail_before_any_put(self):
		doc = self.with_attachments()
		tampered = doc.attachments[1].file
		with patch(
			"docarchive.content.read_bytes",
			side_effect=lambda url: b"tampered" if url == tampered else original(url),
		):
			self.assertEqual(self.archive(doc), "Failed")
		self.assertEqual(self.puts(), [])
		self.assertIn("Attachment 2", frappe.db.get_value(DOCUMENT, doc.name, "archive_error"))

	def test_missing_attachment_version_is_a_hard_error(self):
		doc = self.with_attachments()
		self.archive(doc)
		doc.reload()
		frappe.db.set_value("Archive Copy", doc.archive_copies[0].name, "status", "Failed")
		frappe.db.set_value(DOCUMENT, doc.name, "archive_status", "Failed")
		fake = self.fake("primary")
		fake.versions[("archive-primary", doc.attachments[0].object_key)][0]["VersionId"] = "replaced"
		self.assertEqual(self.archive(doc), "Failed")
		self.assertIn("recorded version", frappe.db.get_value(DOCUMENT, doc.name, "archive_error"))

	def test_extension_and_sync_cover_attachments(self):
		contract = make_document_type(retention_from="Contract End Date", default_retention_years=10)
		doc = self.with_attachments(document_type=contract.name, contract_end_date="2026-12-31")
		self.assertEqual(self.archive(doc), "Archived")
		doc.reload()
		doc.contract_end_date = "2030-06-30"
		doc.save(ignore_permissions=True)
		self.assertEqual(run.nightly()["extended"], 1)
		doc.reload()
		extended = {
			(bucket, kw["Key"])
			for bucket, fake in self.fakes.items()
			for op, kw in fake.calls
			if op == "put_object_retention"
		}
		keys = [doc.object_key, *(row.object_key for row in doc.attachments), doc.meta_key]
		self.assertEqual(extended, {(f"archive-{n}", k) for n in ("primary", "secondary") for k in keys})
		sidecar = json.loads(self.fake("primary").body("archive-primary", doc.object_key + ".meta.2.json"))
		self.assertEqual(len(sidecar["files"]), 3)
		for key in keys:
			[version] = self.fake("primary").versions[("archive-primary", key)]
			version["ObjectLockMode"] = "COMPLIANCE"
		self.assertEqual(run.sync_mode("primary")["updated"], [doc.name])
		self.assertEqual(run.sync_mode("primary")["updated"], [])
		doc.reload()
		self.assertEqual(
			next(c for c in doc.archive_copies if c.target_name == "primary").lock_mode, "COMPLIANCE"
		)

	def test_sync_needs_every_attachment(self):
		doc = self.with_attachments()
		self.archive(doc)
		doc.reload()
		for key in (doc.object_key, doc.meta_key, doc.attachments[0].object_key):
			self.fake("primary").versions[("archive-primary", key)][0]["ObjectLockMode"] = "COMPLIANCE"
		self.assertEqual(run.sync_mode("primary")["mismatched"], [doc.name])

	def test_locked_objects_lists_every_version(self):
		doc = self.with_attachments()
		self.archive(doc)
		doc.reload()
		copy = doc.archive_copies[0]
		objects = run.locked_objects(doc, copy)
		self.assertEqual(len(objects), 4)
		self.assertTrue(all(version for _key, version, _sha in objects))


original = content.read_bytes


class TestUploadAttachments(IntegrationTestCase):
	def setUp(self):
		make_settings(default_own_company=make_own_company().name)
		self.type = make_document_type()
		frappe.set_user(make_user("docarchive-attachments@example.com", [INTEGRATION_ROLE]))
		self.addCleanup(frappe.set_user, "Administrator")
		self.addCleanup(setattr, frappe.local, "request", None)

	def upload(self, **fields):
		return api.upload_document(
			document_type=self.type.slug,
			document_date="2026-03-15",
			source_party_id="7",
			**fields,
		)

	def multipart(self, files, roles=()):
		data = MultiDict(
			[
				("file", (io.BytesIO(pdf(unique("main").encode())), "invoice.pdf")),
				*(("attachments[]", (io.BytesIO(body), name)) for name, body in files),
				*(("attachment_roles[]", role) for role in roles),
			]
		)
		environ = EnvironBuilder(method="POST", data=data, path="/api/method/x").get_environ()
		frappe.local.request = Request(environ)
		frappe.local.request_ip = "127.0.0.1"

	def test_multipart_attachments(self):
		xml = XML + unique("x").encode()
		self.multipart([("invoice.xml", xml), ("hours.pdf", pdf(b"hours"))], roles=["structured-invoice-xml"])
		result = self.upload()
		self.assertEqual(
			[(a["role"], a["title"]) for a in result["attachments"]],
			[("Structured Invoice (XML)", "invoice"), ("Attachment", "hours")],
		)
		frappe.set_user("Administrator")
		doc = frappe.get_doc(DOCUMENT, result["name"])
		self.assertEqual(content.read_bytes(doc.attachments[0].file), xml)
		for row in doc.attachments:
			file = frappe.get_doc("File", {"file_url": row.file, "attached_to_name": doc.name})
			self.assertEqual((file.is_private, file.attached_to_doctype), (1, DOCUMENT))

	def test_json_attachments(self):
		body = pdf(unique("annex").encode())
		result = self.upload(
			file_name="invoice.pdf",
			content_base64=base64.b64encode(pdf(unique("main").encode())).decode(),
			attachments=[
				{
					"file_name": "annex.pdf",
					"content_base64": base64.b64encode(body).decode(),
					"role": "Proof of Service",
					"title": "Hours March",
				}
			],
		)
		self.assertEqual(result["attachments"][0]["role"], "Proof of Service")
		self.assertEqual(result["attachments"][0]["content_sha256"], content.sha256(body))

	def test_invalid_attachments_are_refused(self):
		main = base64.b64encode(pdf(unique("main").encode())).decode()
		good = base64.b64encode(pdf(b"annex")).decode()
		cases = {
			"file type": [{"file_name": "page.html", "content_base64": good}],
			"empty": [{"file_name": "a.pdf", "content_base64": ""}],
			"base64": [{"file_name": "a.pdf", "content_base64": "!!"}],
			"role": [{"file_name": "a.pdf", "content_base64": good, "role": "Secret"}],
			"slash": [{"file_name": "../a.pdf", "content_base64": good}],
			"shape": ["a.pdf"],
			"too many": [
				{"file_name": f"{n}.pdf", "content_base64": good} for n in range(api.MAX_ATTACHMENTS + 1)
			],
		}
		for case, attachments in cases.items():
			with self.subTest(case), self.assertRaises(api.UploadError):
				self.upload(file_name="invoice.pdf", content_base64=main, attachments=attachments)
		self.assertFalse(frappe.db.exists("File", {"file_name": "page.html", "owner": frappe.session.user}))

	def json_upload(self, main, *bodies, **fields):
		return self.upload(
			file_name="invoice.pdf",
			content_base64=base64.b64encode(main).decode(),
			attachments=[
				{"file_name": f"annex-{n}.pdf", "content_base64": base64.b64encode(body).decode()}
				for n, body in enumerate(bodies)
			],
			**fields,
		)

	def shas(self, name):
		frappe.set_user("Administrator")
		shas = [row.content_sha256 for row in frappe.get_doc(DOCUMENT, name).attachments]
		frappe.set_user("docarchive-attachments@example.com")
		return shas

	def test_same_set_exists_and_new_attachments_join_an_open_document(self):
		main, first, second = (pdf(unique(x).encode()) for x in ("main", "first", "second"))
		created = self.json_upload(main, first)
		for bodies in ((first,), ()):
			again = self.json_upload(main, *bodies)
			self.assertEqual((again["status"], again["name"]), ("exists", created["name"]))
		updated = self.json_upload(main, first, second)
		self.assertEqual((updated["status"], updated["name"]), ("updated", created["name"]))
		self.assertEqual(self.shas(created["name"]), [content.sha256(first), content.sha256(second)])
		self.assertEqual(self.json_upload(main, second, first)["status"], "exists")

	def test_new_attachments_for_an_archived_document_make_a_replacement(self):
		main, first, second = (pdf(unique(x).encode()) for x in ("main", "first", "second"))
		created = self.json_upload(main, first, source_key=unique("billing:invoice"))
		frappe.db.set_value(DOCUMENT, created["name"], "archive_started_at", frappe.utils.now_datetime())
		replaced = self.json_upload(main, second, source_key=None)
		self.assertEqual((replaced["status"], replaced["replaces"]), ("replaced", created["name"]))
		self.assertEqual(self.shas(replaced["name"]), [content.sha256(second)])
		self.assertEqual(self.shas(created["name"]), [content.sha256(first)])
		again = self.json_upload(main, second)
		self.assertEqual((again["status"], again["name"]), ("exists", replaced["name"]))
