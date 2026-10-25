import hashlib

import frappe
from frappe import _

from docarchive import audit, retention, run, settings, store
from docarchive.document_archive.doctype.business_document.business_document import missing_fields

DOCUMENT = run.DOCUMENT
EVENT = "docarchive_archive_now"


def names_of(names) -> list[str]:
	names = frappe.parse_json(names) if isinstance(names, str) and names.startswith("[") else names
	names = [names] if isinstance(names, str) else list(names or [])
	return list(dict.fromkeys(names))


def refusal(doc) -> str | None:
	if not settings.get().enabled:
		return _("Archiving is switched off.")
	if doc.archive_started_at:
		return _("Archiving has already started.")
	if not frappe.db.get_value("Business Document Type", doc.document_type, "archive"):
		return _("Documents of this type are not archived.")
	missing = missing_fields(doc)
	if missing:
		return _("Missing before archiving: {0}").format(", ".join(_(field) for field in missing))
	return None


def check(name):
	doc = frappe.get_doc(DOCUMENT, name)
	doc.check_permission("write")
	return doc


@frappe.whitelist()
def preview(names) -> list[dict]:
	configured = store.targets() if settings.get().enabled else None
	modes = sorted({target.lock_mode for target in configured or []})
	result = []
	for name in names_of(names):
		doc = check(name)
		reason = refusal(doc) or (None if configured else _("No storage target is configured."))
		row = {"name": doc.name, "title": doc.title, "eligible": not reason, "reason": reason, "modes": modes}
		if not reason:
			try:
				row["retain_until"] = retention.retain_until(doc).isoformat()
			except frappe.ValidationError as e:
				row.update(eligible=False, reason=str(e))
		result.append(row)
	return result


@frappe.whitelist(methods=["POST"])
def archive_now(names) -> dict:
	names = names_of(names)
	queued, started, refused = [], [], {}
	for name in names:
		doc = check(name)
		reason = refusal(doc)
		if doc.archive_started_at:
			started.append(name)
		elif reason:
			refused[name] = reason
		else:
			queued.append(name)
	if refused and not (queued or started):
		frappe.throw("\n".join(f"{name}: {reason}" for name, reason in refused.items()))
	if queued:
		frappe.enqueue(
			"docarchive.archive_now.run_job",
			queue="long",
			job_id="docarchive-now-" + hashlib.sha256(",".join(sorted(queued)).encode()).hexdigest()[:16],
			deduplicate=True,
			enqueue_after_commit=True,
			names=queued,
			user=frappe.session.user,
		)
	return {"queued": queued, "started": started, "refused": refused}


def run_job(names, user=None):
	targets = store.targets() if settings.get().enabled else None
	outcomes = {}
	with audit.batch(audit.ARCHIVE_NOW) as documents:
		for name in names:
			documents.start(DOCUMENT, name)
			doc = frappe.get_doc(DOCUMENT, name)
			if (
				not targets
				or doc.archive_status == run.ARCHIVED
				or (refusal(doc) and not doc.archive_started_at)
			):
				status = doc.archive_status
				outcomes[name] = ("skipped", skip_reason(doc, targets))
			else:
				status = run.archive_document(name, targets, outcomes)
			documents.done(DOCUMENT, name, *outcomes[name])
			frappe.publish_realtime(
				EVENT, {"name": name, "status": status}, user=user, doctype=DOCUMENT, docname=name
			)


def skip_reason(doc, targets) -> str:
	if not targets:
		return "not_configured"
	return "already_archived" if doc.archive_status == run.ARCHIVED else "refused"
