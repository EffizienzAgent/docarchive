import hashlib
import json
from datetime import UTC, datetime

import frappe
from frappe import _
from frappe.utils import cint, format_date, now_datetime
from frappe.utils.global_search import delete_for_document

from docarchive import audit, retention, run, store
from docarchive.legal_hold import (
	OFF,
	admin_refusal,
	checked_names,
	clean_reason,
	targets_by_name,
)
from docarchive.permissions import ADMIN_ROLE

DOCUMENT = run.DOCUMENT
BULK_WORD = "LÖSCHEN"
EVENT = "docarchive_deletion"
REPORT = "Expired Documents"
STRIPPED = (
	"file",
	"document_number",
	"contract_end_date",
	"source_system",
	"source_account",
	"source_party_type",
	"source_party_id",
	"source_party_name",
	"source_party_email",
	"source_doctype",
	"source_name",
	"source_key",
	"thread_key",
	"mail_message_id",
	"mail_in_reply_to",
	"mail_references",
	"content_sha256",
	"archive_sha256",
	"archive_size",
	"object_key",
	"meta_key",
	"archive_meta",
	"extension_meta",
	"retention_history",
	"archive_error",
	"legal_hold_reason",
)
EINVOICE_FIELDS = ("einvoice_data", "einvoice_needs_review", "einvoice_checked")


def is_contract(doc) -> bool:
	if doc.archive_meta:
		counts_from = json.loads(doc.archive_meta)["retention"]["counts_from"]
	else:
		counts_from = frappe.db.get_value("Business Document Type", doc.document_type, "retention_from")
	return counts_from == retention.CONTRACT_END


def latest_until(doc) -> datetime:
	values = [retention.from_db(doc.retain_until)]
	values += [retention.from_db(copy.retain_until) for copy in doc.archive_copies]
	if is_contract(doc) and doc.contract_end_date and doc.archive_meta:
		values.append(retention.retain_until(doc, run.frozen_years(doc)))
	return max(value for value in values if value)


def refusal(doc, early=False) -> str | None:
	copies = doc.archive_copies
	if doc.archive_status == run.DELETED:
		return _("The document is already deleted.")
	if not (doc.archived_at and copies) or any(
		copy.status not in (run.ARCHIVED, run.DELETED) for copy in copies
	):
		return _("The document is not fully archived.")
	if doc.legal_hold or any(copy.legal_hold != OFF for copy in copies):
		return _("The document is under legal hold.")
	if doc.extension_until:
		return _("A retention extension is still running.")
	if is_contract(doc) and not doc.contract_end_date:
		return _("The contract has no end date yet.")
	missing = sorted({key for copy in copies for key in run.missing_versions(copy, doc)})
	if missing:
		return _("Not all object versions are recorded: {0}").format(", ".join(missing))
	if early:
		if any(copy.lock_mode != "GOVERNANCE" for copy in copies):
			return _("Copies locked in COMPLIANCE mode cannot be deleted early.")
	elif latest_until(doc) > datetime.now(UTC):
		return still_locked(doc)
	return None


def still_locked(doc) -> str:
	return _("The retention period runs until {0}.").format(format_date(latest_until(doc).date()))


def candidates(early=False) -> list[str]:
	filters = {"archive_status": run.ARCHIVED, "legal_hold": 0, "extension_until": ("is", "not set")}
	if not early:
		filters["retain_until"] = ("<=", retention.to_db(datetime.now(UTC)))
	return frappe.get_list(
		DOCUMENT, filters=filters, pluck="name", order_by="retain_until asc", limit_page_length=0
	)


def expired_documents() -> list:
	docs = (frappe.get_doc(DOCUMENT, name) for name in candidates())
	return [doc for doc in docs if not refusal(doc)]


@frappe.whitelist()
def due_count() -> int:
	return len(expired_documents())


@frappe.whitelist()
def due_count_card(filters=None) -> dict:
	return {"value": due_count(), "fieldtype": "Int", "route": ["query-report", REPORT]}


def preview_row(doc, by_name, early) -> dict:
	reason = refusal(doc, early) or admin_refusal(doc, by_name)
	until = latest_until(doc) if doc.retain_until else None
	return {
		"name": doc.name,
		"title": doc.title,
		"document_type": doc.document_type,
		"eligible": not reason,
		"reason": reason,
		"retain_until": until.isoformat() if until else None,
		"retain_until_date": format_date(until.date()) if until else None,
		"locked": bool(until and until > datetime.now(UTC)),
		"targets": sorted({copy.target_name for copy in doc.archive_copies}),
	}


@frappe.whitelist()
def preview(names, early=0) -> list[dict]:
	frappe.only_for(ADMIN_ROLE)
	by_name = targets_by_name()
	return [preview_row(readable(name), by_name, cint(early)) for name in checked_names(names)]


def readable(name):
	doc = frappe.get_doc(DOCUMENT, name)
	doc.check_permission("read")
	return doc


def confirm_word(names) -> str:
	return names[0] if len(names) == 1 else BULK_WORD


@frappe.whitelist(methods=["POST"])
def delete_documents(names, confirm=None, early=0, reason=None, typed=None) -> dict:
	frappe.only_for(ADMIN_ROLE)
	if not cint(confirm):
		frappe.throw(_("Please confirm the deletion."))
	names, early = checked_names(names), cint(early)
	if early:
		reason = clean_reason(reason)
		if (typed or "").strip() != confirm_word(names):
			frappe.throw(_("Please type {0} to confirm.").format(confirm_word(names)))
	refused = [row for row in preview(names, early) if not row["eligible"]]
	if refused:
		frappe.throw("\n".join(f"{row['name']}: {row['reason']}" for row in refused))
	frappe.enqueue(
		"docarchive.expiry.run_job",
		queue="long",
		job_id="docarchive-delete-" + hashlib.sha256(",".join(sorted(names)).encode()).hexdigest()[:16],
		deduplicate=True,
		enqueue_after_commit=True,
		names=names,
		user=frappe.session.user,
		early=early,
		reason=reason if early else None,
	)
	return {"queued": names}


def run_job(names, user, early=0, reason=None) -> dict:
	by_name = targets_by_name()
	results = {}
	with audit.batch(audit.DOCUMENTS_DELETED, {"early": bool(early)}) as documents:
		for name in names:
			documents.start(DOCUMENT, name)
			lock = run.document_lock(name)
			if not lock.acquire(blocking=False):
				results[name] = run.SKIPPED
			else:
				try:
					results[name] = delete_document(name, by_name, user, early, reason)
				finally:
					run.release(lock)
				frappe.publish_realtime(EVENT, {"name": name, "status": results[name]}, user=user)
			documents.done(DOCUMENT, name, results[name].lower())
	return results


def delete_document(name, by_name, user, early=0, reason=None) -> str:
	doc = frappe.get_doc(DOCUMENT, name)
	problem = refusal(doc, early) or admin_refusal(doc, by_name)
	if problem:
		run.log(doc, None, problem)
		return run.SKIPPED
	until = latest_until(doc)
	pending = [copy for copy in doc.archive_copies if copy.status != run.DELETED]
	try:
		for copy in pending:
			for key, version in run.copy_objects(copy, doc):
				store.deletable(by_name[copy.target_name], key, version, early)
	except Exception as e:
		run.log(doc, None, deletion_error(e, early))
		return run.SKIPPED
	delete = store.delete_early if early else store.delete_expired
	for copy in pending:
		target = by_name[copy.target_name]
		try:
			for key, version in run.copy_objects(copy, doc):
				delete(target, key, version)
			copy.update({"status": run.DELETED, "deleted_at": now_datetime(), "error": None})
		except Exception as e:
			copy.error = deletion_error(e, early)
			run.log(doc, target, copy.error)
	if any(copy.status != run.DELETED for copy in doc.archive_copies):
		run.save(doc)
		return run.FAILED
	try:
		strip(doc, user, until, early, reason)
	except Exception as e:
		frappe.db.rollback(save_point=run.SAVEPOINT)
		run.save(doc)
		run.log(doc, None, run.safe_error(e))
		return run.FAILED
	return run.DELETED


def deletion_error(error, early) -> str:
	if store.refused(error):
		if early:
			return _(
				"The storage refused the early deletion. The admin key needs s3:BypassGovernanceRetention."
			)
		return _("The storage refused the deletion. The document cannot be deleted yet.")
	return run.safe_error(error)


def empty(doc, field):
	return 0 if doc.meta.get_field(field).fieldtype in ("Check", "Int") else None


def strip(doc, user, until, early, reason):
	frappe.db.savepoint(run.SAVEPOINT)
	deleted_at = now_datetime()
	frappe.get_doc(
		{
			"doctype": "Document Deletion Log",
			"business_document": doc.name,
			"document_type": doc.document_type,
			"own_company": doc.own_company,
			"counterparty_type": doc.counterparty_type,
			"counterparty": doc.counterparty,
			"retain_until": retention.to_db(until),
			"deleted_at": deleted_at,
			"deleted_by": user,
			"early": 1 if early else 0,
			"reason": reason if early else None,
		}
	).insert(ignore_permissions=True)
	frappe.db.set_value(
		DOCUMENT,
		doc.name,
		{
			**dict.fromkeys(STRIPPED),
			"archive_size": 0,
			**dict.fromkeys(("_comments", "_assign", "_liked_by", "_user_tags")),
			**{field: empty(doc, field) for field in EINVOICE_FIELDS if doc.meta.has_field(field)},
			"title": doc.name,
			"archive_status": run.DELETED,
			"deleted_at": deleted_at,
		},
	)
	for copy in doc.archive_copies:
		versions = sorted(json.loads(copy.sidecar_versions or "{}").values())
		frappe.db.set_value(
			"Archive Copy",
			copy.name,
			{
				"status": run.DELETED,
				"deleted_at": copy.deleted_at,
				"error": None,
				"sidecar_versions": json.dumps(versions),
			},
		)
	for file in frappe.get_all(
		"File", filters={"attached_to_doctype": DOCUMENT, "attached_to_name": doc.name}, pluck="name"
	):
		frappe.delete_doc("File", file, ignore_permissions=True)
	frappe.db.delete("Business Document Attachment", {"parenttype": DOCUMENT, "parent": doc.name})
	for original in frappe.get_all("Mail Original", filters={"business_document": doc.name}, pluck="name"):
		frappe.delete_doc("Mail Original", original, ignore_permissions=True)
	for row in doc.get("attachment_copies") or []:
		frappe.db.set_value("Archive Attachment Copy", row.name, "object_key", None)
	for entry in doc.legal_hold_log:
		frappe.db.set_value("Legal Hold Log Entry", entry.name, {"reason": None, "result": None})
	frappe.db.delete("Version", {"ref_doctype": DOCUMENT, "docname": doc.name})
	frappe.db.delete("Comment", {"reference_doctype": DOCUMENT, "reference_name": doc.name})
	frappe.db.delete("ToDo", {"reference_type": DOCUMENT, "reference_name": doc.name})
	frappe.db.delete("Tag Link", {"document_type": DOCUMENT, "document_name": doc.name})
	delete_for_document(doc)
	frappe.db.commit()
