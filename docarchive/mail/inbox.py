import heapq
from datetime import timedelta
from itertools import islice

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

from docarchive.mail import archiving, events
from docarchive.mail.cleanup import DEFAULT_KEEP_DAYS

ORIGINAL = "Mail Original"
DOCUMENT = "Business Document"
COMMUNICATION = "Communication"
DEFAULT_LIMIT = 50
MAX_LIMIT = 200
CHUNK = 500
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


@frappe.whitelist(methods=["POST"])
def ignore(names) -> dict:
	communications = _checked(names)
	changed = _set_ignored(communications, 1)
	events.changed("ignored", changed, [communications[name] for name in changed])
	return {"ignored": changed}


@frappe.whitelist(methods=["POST"])
def unignore(names) -> dict:
	communications = _checked(names)
	changed = _set_ignored(communications, 0)
	events.changed("unignored", changed, [communications[name] for name in changed])
	return {"unignored": changed}


@frappe.whitelist(methods=["POST"])
def archive_originals(names) -> dict:
	communications = _checked(names)
	return archiving.archive_each(
		[(name, name, communication) for name, communication in communications.items()]
	)


def readable(communications) -> set[str]:
	communications = [name for name in communications if name]
	found = set()
	for start in range(0, len(communications), CHUNK):
		chunk = communications[start : start + CHUNK]
		found.update(
			frappe.get_list(COMMUNICATION, filters={"name": ("in", chunk)}, pluck="name", limit=len(chunk))
		)
	return found


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
		"business_document": ("is", "not set"),
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


def _checked(names) -> dict[str, str]:
	names = archiving._names(names)
	frappe.has_permission(DOCUMENT, "create", throw=True)
	communications = dict(
		frappe.get_all(
			ORIGINAL, filters={"name": ("in", names or [""])}, fields=["name", "communication"], as_list=True
		)
	)
	allowed = readable(communications.values())
	for name in names:
		if communications.get(name) not in allowed:
			frappe.throw(
				_("Mail Original {0} does not exist or you cannot read its e-mail.").format(name),
				frappe.PermissionError,
			)
	return {name: communications[name] for name in names}


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
