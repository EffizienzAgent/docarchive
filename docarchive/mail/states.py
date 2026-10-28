from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import add_to_date, get_datetime

from docarchive import settings
from docarchive.mail import archiving, suggestion
from docarchive.mail.access import readable
from docarchive.mail.cleanup import DEFAULT_KEEP_DAYS, _types_needing_party

ORIGINAL = "Mail Original"
DOCUMENT = "Business Document"
SEALED_STATES = ("Archived", "Deleted")
MAX_NAMES = 500
RUN_HOUR, RUN_MINUTE = 2, 30
ORIGINAL_FIELDS = [
	"name",
	"communication",
	"business_document",
	"automated",
	"internal",
	"ignored",
	"email_account",
	"subject",
	"creation",
	"status",
	"attempts",
]
DOCUMENT_FIELDS = [
	"name",
	"archive_status",
	"retain_until",
	"creation",
	"modified",
	"archive_started_at",
	"counterparty",
	"document_type",
	"hold_on_archive",
]


def statuses(names: list[str]) -> dict[str, dict]:
	names = list(dict.fromkeys(names))[:MAX_NAMES]
	allowed = readable(names)
	originals = _by_communication(allowed)
	documents = _documents(originals.values())
	visible = _visible(documents)
	context = frappe._dict(
		accounts={row.email_account: row for row in settings.get().mail_accounts},
		words=suggestion.keywords(),
		files=suggestion.attachment_names(_suggestable(originals.values(), settings.get().mail_accounts)),
		types=set(_types_needing_party()) if documents else set(),
		can_archive=bool(frappe.has_permission(DOCUMENT, "create")),
		can_write=bool(documents) and bool(frappe.has_permission(DOCUMENT, "write")),
		visible=visible,
		documents=documents,
	)
	return {name: _status(name in allowed, originals.get(name), context) for name in names}


def next_run_after(moment):
	moment = get_datetime(moment)
	run = moment.replace(hour=RUN_HOUR, minute=RUN_MINUTE, second=0, microsecond=0)
	return run if run >= moment else run + timedelta(days=1)


def reasons_for(original, file_names, words) -> list[str]:
	if original.automated or original.internal:
		return []
	return suggestion.reasons(original.subject, file_names, words)


def _by_communication(communications):
	rows = frappe.get_all(
		ORIGINAL,
		filters={"communication": ("in", list(communications) or [""])},
		fields=ORIGINAL_FIELDS,
		order_by="creation asc, name asc",
	)
	return {row.communication: row for row in reversed(rows)}


def _documents(originals):
	names = list({row.business_document for row in originals if row.business_document})
	rows = frappe.get_all(DOCUMENT, filters={"name": ("in", names or [""])}, fields=DOCUMENT_FIELDS)
	return {row.name: row for row in rows}


def _visible(documents):
	if not (documents and frappe.has_permission(DOCUMENT, "read")):
		return set()
	return set(
		frappe.get_list(
			DOCUMENT, filters={"name": ("in", list(documents))}, pluck="name", limit=len(documents)
		)
	)


def _suggestable(originals, accounts):
	manual = {row.email_account for row in accounts if row.mode == "Manual"}
	return [
		row.communication
		for row in originals
		if row.email_account in manual
		and not (row.business_document or row.ignored or row.automated or row.internal)
	]


def _status(readable_by_user, original, context):
	if not readable_by_user:
		return _blank(_("You cannot read this e-mail."), context)
	if not original:
		return _blank(archiving.refusal(None), context)
	document = context.documents.get(original.business_document)
	row = context.accounts.get(original.email_account)
	if not original.business_document and (not row or row.mode == "Off"):
		return _blank(archiving.refusal_for(original.status, original.attempts), context)
	reasons = []
	if not (original.business_document or original.ignored) and row and row.mode == "Manual":
		reasons = reasons_for(original, context.files.get(original.communication, []), context.words)
	return {
		**_blank(archiving.refusal_for(original.status, original.attempts), context),
		"original": True,
		"archived": bool(original.business_document),
		**_document_fields(document, context),
		"automated": bool(original.automated),
		"internal": bool(original.internal),
		"mail_original": original.name,
		"ignored": bool(original.ignored),
		"state": _state(original, document, reasons),
		"suggested": bool(reasons),
		"suggestion_reasons": reasons,
		"expires_on": _expires_on(original, document, row, context),
		"sealed_from": _sealed_from(document),
		"can_discard": _can_discard(document, context),
	}


def _blank(reason, context):
	return {
		"original": False,
		"archived": False,
		"business_document": None,
		"automated": False,
		"internal": False,
		"archive_status": None,
		"retain_until": None,
		"reason": reason,
		"mail_original": None,
		"ignored": False,
		"state": "none",
		"suggested": False,
		"suggestion_reasons": [],
		"expires_on": None,
		"sealed_from": None,
		"can_archive": context.can_archive,
		"can_discard": False,
	}


def _document_fields(document, context):
	if not document or document.name not in context.visible:
		return {}
	return {
		"business_document": document.name,
		"archive_status": document.archive_status,
		"retain_until": document.retain_until,
	}


def _state(original, document, reasons):
	if original.business_document:
		sealed = document and document.archive_status in SEALED_STATES
		return "archived" if sealed else "filed"
	if original.ignored:
		return "ignored"
	return "suggested" if reasons else "not_archived"


def _keep_days(row):
	return (row and row.keep_days) or DEFAULT_KEEP_DAYS


def _expires_on(original, document, row, context):
	keep = timedelta(days=_keep_days(row))
	if not original.business_document:
		return (original.creation + keep).date()
	if document and _removable(document, context):
		return (document.creation + keep).date()
	return None


def _removable(document, context):
	return not (
		document.archive_started_at
		or document.counterparty
		or document.hold_on_archive
		or document.document_type not in context.types
	)


def _sealed_from(document):
	if not document or document.archive_started_at or not settings.get().enabled:
		return None
	return next_run_after(add_to_date(document.modified, hours=settings.get().min_age_hours or 0))


def _can_discard(document, context):
	if not document or document.archive_started_at or document.hold_on_archive:
		return False
	return context.can_write and document.name in context.visible
