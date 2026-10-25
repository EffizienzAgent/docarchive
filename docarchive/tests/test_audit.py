import hashlib
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import frappe
from frappe.utils import now_datetime

from docarchive import audit, expiry, extension, legal_hold, run, store
from docarchive.permissions import ADMIN_ROLE
from docarchive.tests.factories import make_document_type, make_outsider, make_settings, make_user
from docarchive.tests.hold_case import DOCUMENT, HoldTestCase

LOG = audit.LOG
ADMIN = "audit-admin@example.test"


class AuditTestCase(HoldTestCase):
	def setUp(self):
		super().setUp()
		for doctype in (LOG, audit.DOCUMENT_ROW, audit.COPY_ROW):
			frappe.db.delete(doctype)
		frappe.db.delete("Notification Log", {"subject": ("like", "%audit%")})
		self.queued = []
		self.enterContext(patch("frappe.enqueue", side_effect=lambda *a, **kw: self.queued.append(kw)))
		frappe.db.after_commit.reset()
		self.enterContext(patch.object(frappe.db, "commit", side_effect=self.commit))

	def commit(self, *args, **kwargs):
		frappe.db.after_commit.run()

	def reconcile(self):
		return audit.reconcile(store.targets())

	def log(self, *args, **kwargs):
		name = audit.log(*args, **kwargs)
		frappe.db.commit()
		return frappe.get_doc(LOG, name)

	def tearDown(self):
		frappe.set_user("Administrator")
		super().tearDown()

	def entries(self, action=None):
		filters = {"action": action} if action else {}
		return [frappe.get_doc(LOG, name) for name in frappe.get_all(LOG, filters=filters, pluck="name")]

	def outcomes(self, entry):
		return {row.document_name: (row.outcome, row.reason) for row in entry.documents}

	def stored(self, target_name, entry):
		return self.fake(target_name).versions[(f"archive-{target_name}", entry.object_key)]


class TestLog(AuditTestCase):
	def test_entry_is_written_locked_to_every_target(self):
		name = self.log(
			"test.action",
			"Archive Settings",
			"Archive Settings",
			{"count": 2},
			[(DOCUMENT, "DOC-1", "archived"), (DOCUMENT, "DOC-2", "failed", "storage:AccessDenied")],
			write=True,
		)
		entry, name = name, name.name
		self.assertEqual(entry.status, "Written")
		self.assertEqual(
			self.outcomes(entry),
			{"DOC-1": ("archived", None), "DOC-2": ("failed", "storage:AccessDenied")},
		)
		self.assertRegex(entry.object_key, r"^_audit/\d{4}/\d{2}/\d{8}T\d{12}Z-[0-9a-f]{32}\.json$")
		until = datetime(now_datetime().year + 11, 1, 1, tzinfo=UTC)
		for target in ("primary", "secondary"):
			[version] = self.stored(target, entry)
			self.assertEqual(version["Body"], entry.body.encode())
			self.assertEqual(
				(version["ObjectLockMode"], version["ObjectLockRetainUntilDate"]), ("GOVERNANCE", until)
			)
		body = json.loads(entry.body)
		self.assertEqual(
			(body["id"], body["user"], body["action"], body["details"], body["reference"]),
			(
				name,
				"Administrator",
				"test.action",
				{"count": 2},
				{"doctype": "Archive Settings", "name": "Archive Settings"},
			),
		)
		self.assertEqual({c.target_name for c in entry.copies}, {"primary", "secondary"})

	def test_lock_years_and_mode_follow_the_settings(self):
		self.configure(["primary"], primary="COMPLIANCE")
		frappe.db.set_single_value("Archive Settings", "audit_retention_years", 3)
		entry = self.log("test.action", write=True)
		[version] = self.stored("primary", entry)
		until = datetime(now_datetime().year + 4, 1, 1, tzinfo=UTC)
		self.assertEqual(
			(version["ObjectLockMode"], version["ObjectLockRetainUntilDate"]), ("COMPLIANCE", until)
		)

	def test_entries_cannot_be_changed_or_deleted(self):
		entry = self.log("test.action", write=True)
		entry.action = "other"
		with self.assertRaises(frappe.ValidationError):
			entry.save(ignore_permissions=True)
		entry.reload()
		entry.flags.audit_write = True
		entry.action = "other"
		with self.assertRaises(frappe.ValidationError):
			entry.save(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc(LOG, entry.name, ignore_permissions=True)
		perms = {(p.role, p.write, p.create, p.delete) for p in frappe.get_meta(LOG).permissions}
		self.assertEqual(perms, {("System Manager", 0, 0, 0), (ADMIN_ROLE, 0, 0, 0)})

	def test_without_storage_work_a_background_job_writes_the_entry(self):
		entry = self.log("test.action")
		self.assertEqual(entry.status, "Pending")
		self.assertEqual(self.fake("primary").audit_puts(), [])
		[job] = self.queued
		self.assertEqual((job["job_id"], job["enqueue_after_commit"]), ("docarchive-audit-flush", True))
		self.assertEqual(audit.flush(), {"written": 1, "pending": 0})
		self.assertEqual(frappe.db.get_value(LOG, entry.name, "status"), "Written")

	def test_nothing_leaves_the_site_while_archiving_is_off(self):
		self.configure(["primary"], enabled=0)
		entry = self.log("test.action", write=True)
		self.assertEqual((entry.status, self.queued), ("Pending", []))
		self.assertEqual(audit.flush(), {"written": 0, "pending": 0})
		self.assertEqual(self.fake("primary").audit_puts(), [])

	def test_unreachable_target_is_retried(self):
		self.fake("secondary").fail = {"put_object"}
		entry = self.log("test.action", write=True)
		self.assertEqual((entry.status, [c.target_name for c in entry.copies]), ("Pending", ["primary"]))
		self.fake("secondary").fail = set()
		self.assertEqual(audit.flush(), {"written": 1, "pending": 0})
		self.assertEqual(len(self.fake("primary").audit_puts()), 1)
		self.assertEqual(len(self.stored("secondary", entry)), 1)

	def test_new_target_gets_older_entries(self):
		self.configure(["primary"])
		entry = self.log("test.action", write=True)
		self.configure(["primary", "secondary"])
		result = audit.nightly(store.targets())
		self.assertTrue(result["ok"])
		self.assertEqual(frappe.db.count(LOG, {"status": "Pending"}), 0)
		self.assertEqual(len(self.stored("secondary", entry)), 1)

	def test_existing_object_with_other_content_is_not_overwritten(self):
		entry = self.log("test.action")
		target = store.targets()[0]
		store.put_locked(target, entry.object_key, b"forged", "GOVERNANCE", audit.retain_until(entry.at))
		audit.flush()
		self.assertEqual(frappe.db.get_value(LOG, entry.name, "status"), "Pending")
		self.assertNotIn(target.name, {c.target_name for c in frappe.get_doc(LOG, entry.name).copies})


class TestTriggers(AuditTestCase):
	def test_nightly_run_is_one_entry_with_outcomes(self):
		good, bad = self.document(), self.document()
		frappe.db.set_value(DOCUMENT, bad.name, "content_sha256", "0" * 64, update_modified=False)
		run.nightly()
		[entry] = self.entries(audit.ARCHIVE_RUN)
		outcomes = self.outcomes(entry)
		self.assertEqual(outcomes[good.name], ("archived", None))
		self.assertEqual(outcomes[bad.name], ("failed", "checksum"))
		self.assertEqual(entry.status, "Written")

	def test_storage_failure_reason_is_a_code(self):
		doc = self.document()
		self.fake("secondary").fail = {"put_object"}
		run.nightly()
		[entry] = self.entries(audit.ARCHIVE_RUN)
		self.assertEqual(self.outcomes(entry)[doc.name], ("failed", "storage:InternalError"))
		self.assertEqual(entry.status, "Pending")

	def test_run_without_changes_leaves_no_entry(self):
		run.nightly()
		self.assertEqual(self.entries(audit.ARCHIVE_RUN), [])

	def test_legal_hold_set_and_release(self):
		first, second = self.archived(), self.archived()
		legal_hold.set_legal_hold([first.name, second.name], "Klage")
		[entry] = self.entries(audit.LEGAL_HOLD_SET)
		self.assertEqual(self.outcomes(entry), {first.name: ("held", None), second.name: ("held", None)})
		self.assertNotIn("Klage", entry.body)
		legal_hold.release_legal_hold([first.name], "Ende", confirm=1)
		[released] = self.entries(audit.LEGAL_HOLD_RELEASED)
		self.assertEqual(self.outcomes(released), {first.name: ("released", None)})

	def test_refused_hold_leaves_no_entry(self):
		fresh = self.document()
		legal_hold.set_legal_hold([fresh.name], "x")
		self.assertEqual(self.entries(audit.LEGAL_HOLD_SET), [])

	def test_deletion_run(self):
		doc = self.expire(self.archived())
		self.assertEqual(expiry.run_job([doc.name], "Administrator"), {doc.name: "Deleted"})
		[entry] = self.entries(audit.DOCUMENTS_DELETED)
		self.assertEqual(self.outcomes(entry), {doc.name: ("deleted", None)})
		self.assertEqual(json.loads(entry.details), {"early": False, "counts": {"deleted": 1}})
		self.assertEqual(entry.status, "Written")

	def test_manual_extension(self):
		doc = self.archived()
		extension.extend(doc.name, extension.ADD_YEARS, years=2)
		[entry] = self.entries(audit.RETENTION_EXTENDED)
		self.assertEqual(self.outcomes(entry), {doc.name: ("extended", None)})

	def test_settings_change_records_field_names_only(self):
		frappe.db.delete(LOG)
		make_settings(enabled=1, default_own_company=self.company, rules=[(self.company, self.type.name, 12)])
		[entry] = self.entries(audit.SETTINGS_CHANGED)
		self.assertEqual(json.loads(entry.details), {"created": False, "fields": ["retention_rules"]})
		self.assertEqual(entry.status, "Pending")
		self.assertEqual(self.fake("primary").audit_puts(), [])

	def test_document_type_change(self):
		self.type.default_retention_years = 11
		self.type.save(ignore_permissions=True)
		entry = self.entries(audit.SETTINGS_CHANGED)[-1]
		self.assertEqual(
			(entry.reference_name, json.loads(entry.details)["fields"]),
			(self.type.name, ["default_retention_years"]),
		)

	def test_metadata_change_before_archiving(self):
		doc = self.document()
		doc.document_number = "INV-NEW"
		doc.save(ignore_permissions=True)
		[entry] = self.entries(audit.DOCUMENT_CHANGED)
		self.assertEqual(json.loads(entry.details), {"fields": {doc.name: ["document_number"]}})
		self.assertNotIn("INV-NEW", entry.body)
		self.assertEqual(self.outcomes(entry), {doc.name: ("changed", None)})

	def test_bulk_changes_in_one_transaction_are_one_entry(self):
		docs = [self.document() for _ in range(3)]
		frappe.db.commit()
		for doc in docs:
			doc.document_number = "INV-BULK"
			doc.save(ignore_permissions=True)
		docs[0].document_date = "2026-03-16"
		docs[0].save(ignore_permissions=True)
		[entry] = self.entries(audit.DOCUMENT_CHANGED)
		self.assertEqual(set(self.outcomes(entry)), {doc.name for doc in docs})
		self.assertEqual(
			json.loads(entry.details)["fields"],
			{
				docs[0].name: ["document_date", "document_number"],
				docs[1].name: ["document_number"],
				docs[2].name: ["document_number"],
			},
		)
		self.assertEqual(entry.sha256, hashlib.sha256(entry.body.encode()).hexdigest())
		self.assertEqual(json.loads(entry.body)["details"], json.loads(entry.details))
		frappe.db.commit()
		audit.flush()
		self.assertEqual(frappe.db.get_value(LOG, entry.name, "status"), "Written")
		self.assertEqual(self.reconcile()["mismatches"], [])

	def test_next_transaction_starts_a_new_entry(self):
		first, second = self.document(), self.document()
		frappe.db.commit()
		first.document_number = "INV-A"
		first.save(ignore_permissions=True)
		frappe.db.commit()
		second.document_number = "INV-B"
		second.save(ignore_permissions=True)
		self.assertEqual(len(self.entries(audit.DOCUMENT_CHANGED)), 2)

	def test_full_rollback_closes_the_open_entry(self):
		doc = self.document()
		frappe.db.commit()
		doc.document_number = "INV-1"
		doc.save(ignore_permissions=True)
		with patch("frappe.db.sql"):
			frappe.db.rollback()
		self.assertIsNone(frappe.local.docarchive_change_entry)

	def test_other_doctype_starts_a_new_entry(self):
		doc = self.document()
		frappe.db.commit()
		doc.document_number = "INV-1"
		doc.save(ignore_permissions=True)
		audit.note_change("Contact", doc.name, ["first_name"])
		self.assertEqual(len(self.entries(audit.DOCUMENT_CHANGED)), 2)

	def test_change_rolled_back_to_a_savepoint_is_not_logged(self):
		doc = self.document()
		frappe.db.commit()
		frappe.db.savepoint("audit_test")
		doc.document_number = "INV-GONE"
		doc.save(ignore_permissions=True)
		frappe.db.rollback(save_point="audit_test")
		self.assertEqual(self.entries(audit.DOCUMENT_CHANGED), [])
		other = self.document()
		other.document_number = "INV-KEPT"
		other.save(ignore_permissions=True)
		[entry] = self.entries(audit.DOCUMENT_CHANGED)
		self.assertEqual(set(self.outcomes(entry)), {other.name})


class TestCommit(AuditTestCase):
	def test_object_is_written_only_after_commit(self):
		name = audit.log("test.action", write=True)
		self.assertEqual(self.fake("primary").audit_puts(), [])
		frappe.db.commit()
		self.assertEqual(frappe.db.get_value(LOG, name, "status"), "Written")

	def test_rolled_back_entry_leaves_no_object(self):
		audit.log("test.action", write=True)
		with patch("frappe.db.sql"):
			frappe.db.rollback()
		frappe.db.commit()
		self.assertEqual(self.fake("primary").audit_puts(), [])

	def test_aborted_batch_still_logs_what_it_did(self):
		with patch("frappe.db.rollback"), self.assertRaises(RuntimeError):
			with audit.batch("test.batch", {"how": "x"}) as documents:
				documents.append((DOCUMENT, "DOC-1", "deleted"))
				raise RuntimeError("timeout")
		[entry] = self.entries("test.batch")
		self.assertEqual(json.loads(entry.details), {"how": "x", "counts": {"deleted": 1}, "aborted": True})
		self.assertEqual(self.outcomes(entry), {"DOC-1": ("deleted", None)})

	def test_aborted_nightly_run_logs_archived_documents(self):
		done, other = self.document(), self.document()
		real = run.archive_document

		def archive(name, targets, outcomes=None):
			if name == other.name:
				raise RuntimeError("killed")
			return real(name, targets, outcomes)

		with patch("docarchive.run.archive_document", side_effect=archive), patch("frappe.db.rollback"):
			with self.assertRaises(RuntimeError):
				run.nightly()
		[entry] = self.entries(audit.ARCHIVE_RUN)
		self.assertEqual(
			self.outcomes(entry), {done.name: ("archived", None), other.name: ("interrupted", None)}
		)
		details = json.loads(entry.details)
		self.assertEqual((details["aborted"], details["counts"]), (True, {"archived": 1, "interrupted": 1}))


class TestLinks(AuditTestCase):
	def test_entries_do_not_block_deleting_a_document(self):
		doc = self.document()
		doc.document_number = "INV-NEW"
		doc.save(ignore_permissions=True)
		[entry] = self.entries(audit.DOCUMENT_CHANGED)
		frappe.delete_doc(DOCUMENT, doc.name, ignore_permissions=True)
		self.assertTrue(frappe.db.exists(LOG, entry.name))


class TestReconcile(AuditTestCase):
	def notifications(self):
		return frappe.get_all(
			"Notification Log", filters={"subject": ("like", "%audit log%")}, fields=["for_user", "subject"]
		)

	def setUp(self):
		super().setUp()
		make_user(ADMIN, [ADMIN_ROLE])

	def reconcile(self):
		return audit.reconcile(store.targets())

	def test_matching_storage_raises_no_alert(self):
		audit.log("test.action", write=True)
		self.assertEqual(self.reconcile()["mismatches"], [])
		self.assertEqual(self.notifications(), [])

	def test_mismatches_are_found_and_alerted_once_a_day(self):
		changed = self.log("test.changed", write=True)
		missing = self.log("test.missing", write=True)
		unwritten = self.log("test.unwritten")
		frappe.db.set_value(LOG, unwritten.name, "at", now_datetime() - timedelta(days=2))
		deleted = self.log("test.deleted", write=True)
		frappe.db.set_value(LOG, changed.name, "action", "test.other", update_modified=False)
		del self.fake("primary").versions[("archive-primary", missing.object_key)]
		frappe.db.delete(audit.DOCUMENT_ROW, {"parent": deleted.name})
		frappe.db.delete(audit.COPY_ROW, {"parent": deleted.name})
		frappe.db.delete(LOG, deleted.name)
		problems = {(m["target"], m["key"], m["problem"]) for m in self.reconcile()["mismatches"]}
		self.assertIn((None, changed.object_key, "changed in database"), problems)
		self.assertIn(("primary", missing.object_key, "missing"), problems)
		self.assertIn(("primary", deleted.object_key, "unknown"), problems)
		self.assertIn(("secondary", deleted.object_key, "unknown"), problems)
		self.assertIn(
			("primary", frappe.db.get_value(LOG, unwritten.name, "object_key"), "unwritten"), problems
		)
		self.assertEqual([n.for_user for n in self.notifications()], [ADMIN])
		self.reconcile()
		self.assertEqual(len(self.notifications()), 1)

	def test_changed_body_in_the_bucket_is_found(self):
		entry = self.log("test.action", write=True)
		[version] = self.stored("primary", entry)
		version["Body"] = b"{}"
		version["Metadata"] = {"sha256": hashlib.sha256(b"{}").hexdigest()}
		problems = {(m["target"], m["problem"]) for m in self.reconcile()["mismatches"]}
		self.assertEqual(problems, {("primary", "differing")})

	def test_failing_head_is_reported_not_raised(self):
		entry = self.log("test.action", write=True)
		frappe.db.commit()
		[version] = self.stored("primary", entry)
		version["Body"] = b"{}"
		self.fake("primary").fail = {"head_object"}
		problems = {(m["target"], m["problem"]) for m in self.reconcile()["mismatches"]}
		self.assertIn(("primary", "unchecked: Storage error InternalError"), problems)

	def test_expired_entries_are_blanked(self):
		old = self.log(
			"test.old", "Communication", "COMM-1", documents=[(DOCUMENT, "DOC-OLD", "archived")], write=True
		)
		kept = self.log("test.kept", write=True)
		frappe.db.set_value(LOG, old.name, "retain_until", datetime(2020, 1, 1))
		self.assertEqual(audit.prune(), 1)
		row = frappe.db.get_value(
			LOG, old.name, ["status", "user", "body", "details", "reference_name", "object_key"], as_dict=True
		)
		self.assertEqual(
			(row.status, row.user, row.body, row.details, row.reference_name),
			("Expired", None, None, None, None),
		)
		self.assertFalse(frappe.db.exists(audit.DOCUMENT_ROW, {"parent": old.name}))
		self.assertEqual(audit.entries_for_export(["DOC-OLD"]), [])
		self.assertEqual(self.reconcile()["mismatches"], [])
		for target in ("primary", "secondary"):
			del self.fake(target).versions[(f"archive-{target}", row.object_key)]
		self.assertEqual(self.reconcile()["mismatches"], [])
		self.assertTrue(frappe.db.exists(LOG, old.name))
		self.assertTrue(frappe.db.exists(LOG, kept.name))
		self.assertEqual(audit.prune(), 0)

	def test_prune_runs_daily_without_archiving(self):
		self.assertIn("docarchive.audit.prune", frappe.get_hooks("scheduler_events")["daily"])

	def test_lifecycle_deletion_after_the_lock_is_fine(self):
		entry = self.log("test.action", write=True)
		frappe.db.set_value(LOG, entry.name, "retain_until", datetime(2020, 1, 1))
		for target in ("primary", "secondary"):
			del self.fake(target).versions[(f"archive-{target}", entry.object_key)]
		self.assertEqual(self.reconcile()["mismatches"], [])


class TestLookups(AuditTestCase):
	def test_entries_for_a_document_newest_first(self):
		doc = self.archived()
		legal_hold.set_legal_hold([doc.name], "x")
		other = self.archived()
		legal_hold.set_legal_hold([other.name], "x")
		rows = audit.entries_for(DOCUMENT, doc.name)
		self.assertEqual([row.action for row in rows], [audit.LEGAL_HOLD_SET])
		self.assertEqual(rows[0].outcome, "held")
		entry = frappe.get_doc(LOG, rows[0].name)
		self.assertEqual(
			[(r.document_doctype, r.document_name) for r in entry.documents], [(DOCUMENT, doc.name)]
		)

	def test_entries_need_read_permission_on_the_document(self):
		doc = self.archived()
		frappe.set_user(make_outsider("audit-outsider@example.test"))
		with self.assertRaises(frappe.PermissionError):
			audit.entries_for(DOCUMENT, doc.name)

	def test_entries_for_export(self):
		doc, other = self.archived(), self.archived()
		legal_hold.set_legal_hold([doc.name], "x")
		names = {entry.name for entry in audit.entries_for_export([doc.name])}
		expected = {row.name for row in audit.entries_for(DOCUMENT, doc.name)}
		self.assertEqual(names, expected)
		self.assertTrue(
			all(
				other.name not in json.loads(e.body)["documents"]
				for e in audit.entries_for_export([doc.name])
			)
		)
		today = str(now_datetime().date())
		self.assertEqual(
			len(audit.entries_for_export([], today, today)), frappe.db.count(LOG, {"document_count": 0})
		)
		self.assertEqual(audit.entries_for_export([], "2000-01-01", "2000-12-31"), [])


class TestStoredBody(AuditTestCase):
	def test_hash_metadata_matches_body(self):
		entry = self.log("test.action", write=True)
		[version] = self.stored("primary", entry)
		self.assertEqual(version["Metadata"]["sha256"], hashlib.sha256(entry.body.encode()).hexdigest())
		self.assertEqual(version["ContentType"], "application/json")

	def test_type_without_archive_change_is_logged(self):
		make_document_type(archive=0)
		self.assertTrue(self.entries(audit.SETTINGS_CHANGED))
