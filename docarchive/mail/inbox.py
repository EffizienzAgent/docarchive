import heapq
from datetime import timedelta
from itertools import islice

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

from docarchive import audit
from docarchive.mail import archiving, events
from docarchive.mail.access import readable
from docarchive.mail.cleanup import DEFAULT_KEEP_DAYS

ORIGINAL = "Mail Original"
DOCUMENT = "Business Document"
DEFAULT_LIMIT = 50
MAX_LIMIT = 200
FIELDS = [
	"name",
	"message_id",
	"subject",
	"sender",
	"to",
	"mail_at",
	"email_account",
	"creation",
	"communication",
	"automated",
	"internal",
]


@frappe.whitelist()
def candidates(limit: int = DEFAULT_LIMIT, expiring_within_days: float | None = None) -> list[dict]:
	if not frappe.has_permission(DOCUMENT, "create"):
		return []
	limit = min(max(cint(limit) or DEFAULT_LIMIT, 1), MAX_LIMIT)
	within = _days(expiring_within_days)
	streams = [_readable_rows(account, keep, within, limit) for account, keep in _accounts()]
	rows = list(islice(heapq.merge(*streams, key=lambda row: row.expires_at), limit))
	found = archiving.parties({row.communication: row.email_account for row in rows})
	titles = _titles(found.values())
	return [_item(row, found[row.communication], titles) for row in rows]


@frappe.whitelist()
def count_candidates(expiring_within_days: float | None = None) -> int:
	if not frappe.has_permission(DOCUMENT, "create"):
		return 0
	within = _days(expiring_within_days)
	return sum(
		len(
			readable(frappe.get_all(ORIGINAL, filters=_filters(account, keep, within), pluck="communication"))
		)
		for account, keep in _accounts()
	)


@frappe.whitelist()
def original_status(name: str) -> dict:
	frappe.has_permission(ORIGINAL, "read", name, throw=True)
	original = frappe.db.get_value(
		ORIGINAL,
		name,
		["communication", "business_document", "ignored", "automated", "internal"],
		as_dict=True,
	)
	document = original.business_document
	in_scope = bool(original.communication) and original.communication in readable([original.communication])
	reason = archiving.refusal(name) if in_scope else _("You cannot read this e-mail.")
	return {
		"archived": bool(document),
		**archiving.document_state(document),
		"ignored": bool(original.ignored),
		"automated": bool(original.automated),
		"internal": bool(original.internal),
		"viewable": not reason,
		"reason": reason,
	}


@frappe.whitelist()
def warnings(names) -> dict:
	communications, _refused = _checked(names)
	rows = frappe.get_all(
		ORIGINAL,
		filters={"name": ("in", list(communications) or [""]), "business_document": None},
		fields=["name", "automated", "internal"],
	)
	flags = {row.name: row for row in rows}
	found = [flags[name] for name in communications if name in flags]
	return {
		"automated": [row.name for row in found if row.automated],
		"internal": [row.name for row in found if row.internal and not row.automated],
	}


@frappe.whitelist(methods=["POST"])
def ignore(names) -> dict:
	return _toggle(names, 1, "ignored", audit.MAIL_IGNORED)


@frappe.whitelist(methods=["POST"])
def unignore(names) -> dict:
	return _toggle(names, 0, "unignored", audit.MAIL_UNIGNORED)


@frappe.whitelist(methods=["POST"])
def archive_originals(names) -> dict:
	communications, refused = _checked(names)
	return archiving.archive_each(
		[(name, name, communication) for name, communication in communications.items()], refused
	)


def _toggle(names, ignored, action, audit_action):
	communications, refused = _checked(names)
	changed = _set_ignored(communications, ignored)
	archiving.audit_log(audit_action, [(ORIGINAL, name, action) for name in changed])
	events.changed(action, changed, [communications[name] for name in changed])
	return {
		action: changed,
		"skipped": [name for name in communications if name not in changed],
		"refused": refused,
	}


def _accounts():
	return [
		(row.email_account, row.keep_days or DEFAULT_KEEP_DAYS)
		for row in frappe.get_single("Archive Settings").mail_accounts
		if row.mode == "Manual"
	]


def _days(value):
	return None if value in (None, "") else flt(value)


def _filters(account, keep, within):
	filters = {
		"email_account": account,
		"status": "Stored",
		"ignored": 0,
		"business_document": None,
		"communication": ("is", "set"),
	}
	if within is not None:
		filters["creation"] = ("<=", now_datetime() + timedelta(days=within - keep))
	return filters


def _readable_rows(account, keep, within, page):
	start = 0
	while True:
		rows = frappe.get_all(
			ORIGINAL,
			filters=_filters(account, keep, within),
			fields=FIELDS,
			order_by="creation asc, name asc",
			offset=start,
			limit=page,
		)
		allowed = readable(row.communication for row in rows)
		for row in rows:
			if row.communication in allowed:
				row.expires_at = row.creation + timedelta(days=keep)
				yield row
		if len(rows) < page:
			return
		start += page


def _titles(found):
	names = {}
	for items in found:
		if len(items) == 1:
			doctype, name = items[0]
			names.setdefault(doctype, set()).add(name)
	titles = {}
	for doctype, members in names.items():
		field = frappe.get_meta(doctype).get_title_field()
		for row in frappe.get_list(
			doctype,
			filters={"name": ("in", list(members))},
			fields=["name", field],
			limit=len(members),
		):
			titles[(doctype, row.name)] = row.get(field)
	return titles


def _item(row, items, titles):
	party = None
	if len(items) == 1:
		doctype, name = items[0]
		party = {"counterparty_type": doctype, "counterparty": name, "title": titles.get(items[0]) or name}
	return {
		"name": row.name,
		"message_id": row.message_id,
		"subject": row.subject,
		"from": row.sender,
		"to": row.to,
		"date": row.mail_at,
		"account": row.email_account,
		"expires_at": row.expires_at,
		"communication": row.communication,
		"automated": bool(row.automated),
		"internal": bool(row.internal),
		"suggested_counterparty": party,
	}


def _checked(names) -> tuple[dict[str, str], dict[str, str]]:
	names = archiving._names(names)
	frappe.has_permission(DOCUMENT, "create", throw=True)
	rows = dict(
		frappe.get_all(
			ORIGINAL, filters={"name": ("in", names or [""])}, fields=["name", "communication"], as_list=True
		)
	)
	allowed = readable(rows.values())
	communications, refused = {}, {}
	for name in names:
		if rows.get(name) in allowed:
			communications[name] = rows[name]
		else:
			refused[name] = _("This e-mail original does not exist or you cannot read its e-mail.")
	return communications, refused


def _set_ignored(communications, ignored):
	changed = frappe.get_all(
		ORIGINAL,
		filters={"name": ("in", list(communications) or [""]), "ignored": 1 - ignored},
		pluck="name",
	)
	changed = [name for name in communications if name in changed]
	if changed:
		frappe.db.set_value(
			ORIGINAL,
			{"name": ("in", changed)},
			{
				"ignored": ignored,
				"ignored_by": frappe.session.user if ignored else None,
				"ignored_at": now_datetime() if ignored else None,
			},
			update_modified=False,
		)
	return changed
