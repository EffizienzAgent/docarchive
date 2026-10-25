import json
from unittest.mock import patch

import frappe

from docarchive import expiry, legal_hold, run, store
from docarchive.document_archive.report.expired_documents import expired_documents as report
from docarchive.tests.factories import eml, make_document_type, make_manager
from docarchive.tests.hold_case import DOCUMENT, PAST, HoldTestCase

LOG = "Document Deletion Log"


class ExpiryTestCase(HoldTestCase):
	def setUp(self):
		super().setUp()
		frappe.db.delete(LOG)
		self.queued = []
		self.enterContext(patch("frappe.enqueue", side_effect=self.enqueue))

	def enqueue(self, *args, **kwargs):
		if kwargs.get("job_id") != "docarchive-audit-flush":
			self.queued.append(kwargs)

	def tearDown(self):
		frappe.set_user("Administrator")
		super().tearDown()

	def deletes(self):
		return sorted(
			(bucket, key, version, bypass)
			for bucket, fake in self.fakes.items()
			for key, version, bypass in fake.deleted()
		)

	def delete(self, names, **kwargs):
		expiry.delete_documents(names, confirm=1, **kwargs)
		job = self.queued.pop()
		return expiry.run_job(job["names"], job["user"], job["early"], job["reason"])

	def contract(self):
		self.type = make_document_type(retention_from="Contract End Date", default_retention_years=10)
		doc = self.archived(contract_end_date="2026-12-31")
		doc.contract_end_date = "2030-06-30"
		doc.save(ignore_permissions=True)
		self.assertEqual(run.nightly()["extended"], 1)
		frappe.db.set_value(
			DOCUMENT, doc.name, {"document_date": "2010-01-01", "contract_end_date": "2012-12-31"}
		)
		return self.expire(doc)


class TestExpiredList(ExpiryTestCase):
	def test_lists_only_expired_unheld_closed_documents(self):
		expired = self.expire(self.archived())
		self.archived()
		held = self.expire(self.archived())
		legal_hold.set_legal_hold([held.name], "x")
		self.type = make_document_type(retention_from="Contract End Date", default_retention_years=10)
		open_contract = self.expire(self.archived())
		closed_contract = self.contract()
		names = [doc.name for doc in expiry.expired_documents()]
		self.assertEqual(sorted(names), sorted([expired.name, closed_contract.name]))
		self.assertEqual(expiry.due_count(), 2)
		self.assertEqual(expiry.due_count_card()["value"], 2)
		self.assertEqual(sorted(row["name"] for row in report.execute()[1]), sorted(names))
		self.assertEqual(
			expiry.refusal(frappe.get_doc(DOCUMENT, open_contract.name)),
			expiry._("The contract has no end date yet."),
		)

	def test_contract_with_later_end_date_is_not_expired(self):
		doc = self.contract()
		frappe.db.set_value(DOCUMENT, doc.name, "contract_end_date", "2030-06-30")
		self.assertTrue(expiry.refusal(frappe.get_doc(DOCUMENT, doc.name)))


class TestDeletion(ExpiryTestCase):
	def test_deletes_exactly_the_recorded_versions_and_strips_the_document(self):
		doc = self.contract()
		recorded = self.recorded(doc)
		self.assertEqual(len(recorded), 6)
		stray = store.put_locked(
			store.targets()[0], doc.object_key, b"other", "GOVERNANCE", "2099-01-02T00:00:00+00:00"
		)
		file_url = doc.file
		self.assertEqual(self.delete([doc.name]), {doc.name: "Deleted"})
		self.assertEqual(self.deletes(), [(*row, False) for row in recorded])
		admin_calls = [
			call
			for fake in self.fakes.values()
			for call, by in zip(fake.calls, fake.callers, strict=False)
			if by == "admin"
		]
		self.assertEqual({operation for operation, _kwargs in admin_calls}, {"delete_object"})
		self.assertTrue(store.head(store.targets()[0], doc.object_key, stray["version_id"]))
		doc.reload()
		self.assertEqual(doc.archive_status, "Deleted")
		self.assertEqual(doc.title, doc.name)
		for field in expiry.STRIPPED:
			self.assertFalse(doc.get(field), field)
		self.assertEqual({copy.status for copy in doc.archive_copies}, {"Deleted"})
		self.assertFalse(frappe.db.exists("File", {"file_url": file_url}))
		self.assertFalse(frappe.db.exists("Version", {"ref_doctype": DOCUMENT, "docname": doc.name}))
		[log] = frappe.get_all(LOG, fields=["*"])
		self.assertEqual(
			(
				log.business_document,
				log.document_type,
				log.own_company,
				log.counterparty,
				log.deleted_by,
				log.early,
			),
			(doc.name, doc.document_type, doc.own_company, doc.counterparty, "Administrator", 0),
		)
		self.assertEqual(str(log.retain_until), "2023-01-02 00:00:00")
		self.assertNotIn(doc.name, [d.name for d in expiry.expired_documents()])
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(DOCUMENT, doc.name).save(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc(LOG, log.name, ignore_permissions=True)

	def test_refused_before_expiry_under_hold_and_for_open_contracts(self):
		locked = self.archived()
		held = self.expire(self.archived())
		legal_hold.set_legal_hold([held.name], "x")
		self.type = make_document_type(retention_from="Contract End Date", default_retention_years=10)
		open_contract = self.expire(self.archived())
		for doc in (locked, held, open_contract):
			with self.subTest(doc=doc.name):
				[row] = expiry.preview([doc.name])
				self.assertFalse(row["eligible"])
				with self.assertRaises(frappe.ValidationError):
					expiry.delete_documents([doc.name], confirm=1)
				self.assertEqual(expiry.run_job([doc.name], "Administrator"), {doc.name: "Skipped"})
		self.assertEqual(self.deletes(), [])
		self.assertEqual(self.queued, [])

	def test_storage_lock_wins_over_the_crm(self):
		doc = self.expire(self.archived())
		self.fake("secondary").versions[("archive-secondary", doc.meta_key)][0][
			"ObjectLockRetainUntilDate"
		] = PAST.replace(year=2099)
		self.assertEqual(self.delete([doc.name]), {doc.name: "Skipped"})
		self.assertEqual(self.deletes(), [])
		self.assertEqual(frappe.get_doc(DOCUMENT, doc.name).archive_status, "Archived")

	def test_partial_failure_is_retried(self):
		doc = self.expire(self.archived())
		self.fake("secondary").deny = {("admin", "delete_object")}
		self.assertEqual(self.delete([doc.name]), {doc.name: "Failed"})
		doc.reload()
		states = {copy.target_name: copy.status for copy in doc.archive_copies}
		self.assertEqual(states, {"primary": "Deleted", "secondary": "Archived"})
		self.assertIn(
			"cannot be deleted yet", next(c.error for c in doc.archive_copies if c.target_name == "secondary")
		)
		self.assertTrue(doc.file)
		self.assertFalse(frappe.db.exists(LOG, {"business_document": doc.name}))
		self.assertIn(doc.name, [d.name for d in expiry.expired_documents()])
		self.assertNotIn(doc.name, run.missing_copies({"primary", "secondary", "third"}))
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(DOCUMENT, doc.name).save(ignore_permissions=True)
		self.fake("secondary").deny = set()
		primary_calls = len(self.fake("primary").object_calls())
		self.assertEqual(self.delete([doc.name]), {doc.name: "Deleted"})
		self.assertEqual(len(self.fake("primary").object_calls()), primary_calls)
		self.assertEqual(frappe.db.count(LOG, {"business_document": doc.name}), 1)

	def test_needs_role_confirmation_and_admin_key(self):
		doc = self.expire(self.archived())
		with self.assertRaises(frappe.ValidationError):
			expiry.delete_documents([doc.name])
		frappe.set_user(make_manager("manager-delete@example.test"))
		for call in (
			lambda: expiry.preview([doc.name]),
			lambda: expiry.delete_documents([doc.name], confirm=1),
		):
			with self.assertRaises(frappe.PermissionError):
				call()
		frappe.set_user("Administrator")
		for admin in ((), ("primary",)):
			with self.subTest(admin=admin):
				self.configure(["primary", "secondary"], admin=admin)
				[row] = expiry.preview([doc.name])
				self.assertIn("is not configured", row["reason"])
				for early in (0, 1):
					with self.assertRaises(frappe.ValidationError):
						expiry.delete_documents(
							[doc.name], confirm=1, early=early, reason="x", typed=doc.name
						)
				self.assertEqual(expiry.run_job([doc.name], "Administrator"), {doc.name: "Skipped"})
		self.assertEqual(self.deletes(), [])


class TestEarlyDeletion(ExpiryTestCase):
	def test_governance_copies_are_deleted_with_bypass(self):
		doc = self.archived()
		for fake in self.fakes.values():
			fake.bypass_allowed = True
		recorded = self.recorded(doc)
		self.assertEqual(
			self.delete([doc.name], early=1, reason="Kunde verlangt Löschung", typed=doc.name),
			{doc.name: "Deleted"},
		)
		self.assertEqual(self.deletes(), [(*row, True) for row in recorded])
		[log] = frappe.get_all(LOG, fields=["*"])
		self.assertEqual((log.early, log.reason), (1, "Kunde verlangt Löschung"))
		self.assertEqual(str(log.retain_until), "2035-01-02 00:00:00")

	def test_needs_reason_and_typed_confirmation(self):
		first, second = self.archived(), self.archived()
		cases = (
			([first.name], "x", "wrong"),
			([first.name], "", first.name),
			([first.name, second.name], "x", first.name),
		)
		for names, reason, typed in cases:
			with self.subTest(typed=typed), self.assertRaises(frappe.ValidationError):
				expiry.delete_documents(names, confirm=1, early=1, reason=reason, typed=typed)
		expiry.delete_documents([first.name, second.name], confirm=1, early=1, reason="x", typed="LÖSCHEN")
		self.assertEqual(len(self.queued), 1)
		self.assertEqual(self.deletes(), [])

	def test_compliance_is_refused_without_any_request(self):
		self.configure(["primary", "secondary"], secondary="COMPLIANCE")
		doc = self.archived()
		calls = sum(len(fake.calls) for fake in self.fakes.values())
		[row] = expiry.preview([doc.name], early=1)
		self.assertFalse(row["eligible"])
		with self.assertRaises(frappe.ValidationError):
			expiry.delete_documents([doc.name], confirm=1, early=1, reason="x", typed=doc.name)
		self.assertEqual(
			expiry.run_job([doc.name], "Administrator", early=1, reason="x"), {doc.name: "Skipped"}
		)
		self.assertEqual(sum(len(fake.calls) for fake in self.fakes.values()), calls)

	def test_storage_compliance_mode_is_checked_before_deleting(self):
		doc = self.archived()
		for fake in self.fakes.values():
			fake.bypass_allowed = True
		self.fake("secondary").versions[("archive-secondary", doc.meta_key)][0]["ObjectLockMode"] = (
			"COMPLIANCE"
		)
		self.assertEqual(self.delete([doc.name], early=1, reason="x", typed=doc.name), {doc.name: "Skipped"})
		self.assertEqual(self.deletes(), [])

	def test_legal_hold_is_refused(self):
		doc = self.archived()
		legal_hold.set_legal_hold([doc.name], "x")
		with self.assertRaises(frappe.ValidationError):
			expiry.delete_documents([doc.name], confirm=1, early=1, reason="x", typed=doc.name)
		self.assertEqual(
			expiry.run_job([doc.name], "Administrator", early=1, reason="x"), {doc.name: "Skipped"}
		)
		self.assertEqual(self.deletes(), [])

	def test_missing_bypass_permission_is_reported(self):
		doc = self.archived()
		self.assertEqual(self.delete([doc.name], early=1, reason="x", typed=doc.name), {doc.name: "Failed"})
		doc.reload()
		self.assertTrue(all("BypassGovernanceRetention" in copy.error for copy in doc.archive_copies))
		self.assertEqual(doc.archive_status, "Archived")
		self.assertFalse(frappe.db.exists(LOG, {"business_document": doc.name}))

	def test_role_required(self):
		doc = self.archived()
		frappe.set_user(make_manager("manager-early@example.test"))
		with self.assertRaises(frappe.PermissionError):
			expiry.delete_documents([doc.name], confirm=1, early=1, reason="x", typed=doc.name)


class TestSidecarPatch(ExpiryTestCase):
	def test_backfills_the_latest_sidecar(self):
		doc = self.contract()
		copy = doc.archive_copies[0]
		expected = json.loads(copy.sidecar_versions)
		frappe.db.set_value("Archive Copy", copy.name, "sidecar_versions", None)
		from docarchive.patches import record_sidecar_versions

		record_sidecar_versions.execute()
		self.assertEqual(
			json.loads(frappe.db.get_value("Archive Copy", copy.name, "sidecar_versions")), expected
		)


class TestReviewFixes(ExpiryTestCase):
	def test_stub_keeps_no_content(self):
		doc = self.expire(self.archived())
		legal_hold.set_legal_hold([doc.name], "Klage Müller GmbH")
		legal_hold.release_legal_hold([doc.name], "Vergleich mit Müller", confirm=1)
		frappe.db.set_value(
			DOCUMENT,
			doc.name,
			{
				"_comments": '[{"comment": "secret"}]',
				"_assign": '["a@example.test"]',
				"_liked_by": '["a"]',
				"_user_tags": ",secret",
			},
		)
		frappe.get_doc(
			{
				"doctype": "Comment",
				"comment_type": "Comment",
				"reference_doctype": DOCUMENT,
				"reference_name": doc.name,
				"content": "secret",
			}
		).insert(ignore_permissions=True)
		with patch("docarchive.expiry.delete_for_document") as global_search:
			self.assertEqual(self.delete([doc.name]), {doc.name: "Deleted"})
		self.assertEqual(global_search.call_args[0][0].name, doc.name)
		stub = frappe.db.get_value(
			DOCUMENT, doc.name, ["_comments", "_assign", "_liked_by", "_user_tags"], as_dict=True
		)
		self.assertEqual(set(stub.values()), {None})
		self.assertFalse(
			frappe.db.exists("Comment", {"reference_doctype": DOCUMENT, "reference_name": doc.name})
		)
		log = frappe.get_doc(DOCUMENT, doc.name).legal_hold_log
		self.assertEqual(
			[(e.action, e.user, bool(e.at), e.reason, e.result) for e in log],
			[("Set", "Administrator", True, None, None), ("Release", "Administrator", True, None, None)],
		)

	def test_contract_rule_is_frozen_at_archive_start(self):
		contract_type = make_document_type(retention_from="Contract End Date", default_retention_years=10)
		self.type = contract_type
		open_contract = self.expire(self.archived())
		frappe.db.set_value("Business Document Type", contract_type.name, "retention_from", "Document Date")
		self.assertEqual(
			expiry.refusal(frappe.get_doc(DOCUMENT, open_contract.name)),
			expiry._("The contract has no end date yet."),
		)
		self.type = make_document_type(default_retention_years=8)
		plain = self.expire(self.archived())
		frappe.db.set_value("Business Document Type", self.type.name, "retention_from", "Contract End Date")
		self.assertIsNone(expiry.refusal(frappe.get_doc(DOCUMENT, plain.name)))

	def test_admin_needs_read_permission(self):
		doc = self.expire(self.archived())
		legal_hold.set_legal_hold([doc.name], "x")
		with patch(
			"docarchive.document_archive.doctype.business_document.business_document.BusinessDocument.has_permission",
			return_value=False,
		):
			for call in (
				lambda: expiry.preview([doc.name]),
				lambda: expiry.delete_documents([doc.name], confirm=1),
				lambda: legal_hold.release_legal_hold([doc.name], "x", confirm=1),
			):
				with self.assertRaises(frappe.PermissionError):
					call()
		self.assertEqual(self.deletes(), [])


class TestDeletionWithAttachments(ExpiryTestCase):
	def test_deletes_attachment_versions_and_strips_their_rows_and_files(self):
		from docarchive.tests.test_attachments import attachment

		doc = self.expire(self.archived(attachments=[attachment()]))
		recorded = self.recorded(doc)
		self.assertEqual(len(recorded), 6)
		attachment_file = doc.attachments[0].file
		self.assertEqual(self.delete([doc.name]), {doc.name: "Deleted"})
		self.assertEqual(self.deletes(), [(*row, False) for row in recorded])
		doc.reload()
		self.assertEqual(doc.attachments, [])
		self.assertFalse(frappe.db.exists("File", {"file_url": attachment_file}))
		self.assertEqual({row.object_key for row in doc.attachment_copies}, {None})
		self.assertTrue(all(row.version_id for row in doc.attachment_copies))

	def test_unrecorded_attachment_version_blocks_deletion(self):
		from docarchive.tests.test_attachments import attachment

		doc = self.expire(self.archived(attachments=[attachment()]))
		frappe.db.set_value("Archive Attachment Copy", doc.attachment_copies[0].name, "version_id", None)
		[row] = expiry.preview([doc.name])
		self.assertIn("Not all object versions are recorded", row["reason"])
		self.assertEqual(expiry.run_job([doc.name], "Administrator"), {doc.name: "Skipped"})
		self.assertEqual(self.deletes(), [])


class TestDeletionOfCorrespondence(ExpiryTestCase):
	def test_deletes_correspondence_with_its_mail_original(self):
		doc = self.expire(self.archived_mail(eml()))
		original = frappe.db.get_value("Mail Original", {"business_document": doc.name}, ["name", "file"])
		self.assertTrue(original)
		self.assertEqual(self.delete([doc.name]), {doc.name: "Deleted"})
		doc.reload()
		self.assertEqual(doc.archive_status, "Deleted")
		for field in ("thread_key", "mail_message_id", "mail_in_reply_to", "mail_references"):
			self.assertFalse(doc.get(field), field)
		self.assertFalse(frappe.db.exists("Mail Original", original[0]))
		self.assertFalse(frappe.db.exists("File", {"file_url": original[1]}))
		self.assertFalse(frappe.db.exists("File", {"attached_to_name": original[0]}))
