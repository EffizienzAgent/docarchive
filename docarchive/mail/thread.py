import frappe
from frappe.utils import cint, get_datetime

from docarchive import settings
from docarchive.mail import access

DOCUMENT = "Business Document"
ORIGINAL = "Mail Original"
COMMUNICATION = "Communication"
FIELDS = [
	"name",
	"title",
	"correspondence_channel",
	"correspondence_direction",
	"correspondence_at",
	"source_party_email",
	"mail_message_id",
	"mail_in_reply_to",
	"mail_references",
]


MAX_MAILS = 200
MAX_ROUNDS = 10
MAX_ROOTS = 5
SAVEPOINT = "docarchive_thread_join"


@frappe.whitelist()
def thread(name: str) -> list[dict]:
	frappe.has_permission(DOCUMENT, "read", name, throw=True)
	start = frappe.db.get_value(
		DOCUMENT, name, [*FIELDS, "thread_key", "thread_root", "own_company"], as_dict=True
	)
	if not start.thread_key:
		return []
	mails = _collect(start)
	mails.sort(key=lambda mail: (mail.correspondence_at or mail.creation, mail.creation), reverse=True)
	seen = {mail.mail_message_id for mail in mails if mail.mail_message_id}
	items = []
	for mail in reversed(mails):
		for message_id in _referenced(mail):
			if message_id not in seen:
				seen.add(message_id)
				items.append(_gap(message_id, start.own_company))
		items.append(_mail(mail))
	return items[::-1]


def _collect(start):
	found = {}
	keys, roots, ids = set(), set(), set()
	frontier = [start]
	for _round in range(MAX_ROUNDS):
		for mail in frontier:
			keys.add(mail.thread_key)
			roots.add(mail.thread_root)
			ids.update(_referenced(mail))
			ids.add(mail.mail_message_id)
		keys.discard(None)
		roots.discard(None)
		ids.discard(None)
		rows = frappe.get_list(
			DOCUMENT,
			filters={"own_company": start.own_company},
			or_filters=[
				["thread_key", "in", list(keys) or [""]],
				["thread_root", "in", list(roots) or [""]],
				["mail_message_id", "in", list(ids) or [""]],
				["mail_in_reply_to", "in", list(ids) or [""]],
			],
			fields=[*FIELDS, "thread_key", "thread_root", "creation"],
			limit=MAX_MAILS,
		)
		frontier = [row for row in rows if row.name not in found]
		for row in frontier:
			found[row.name] = row
		if not frontier or len(found) >= MAX_MAILS:
			break
	return list(found.values())[:MAX_MAILS]


def join_safely(name: str) -> str | None:
	for attempt in range(2):
		try:
			frappe.db.savepoint(SAVEPOINT)
			return join(name)
		except frappe.QueryDeadlockError:
			frappe.db.rollback(save_point=SAVEPOINT)
			if attempt:
				frappe.log_error(title="docarchive thread join")
		except Exception:
			frappe.db.rollback(save_point=SAVEPOINT)
			frappe.log_error(title="docarchive thread join")
			return None
	return None


def join(name: str) -> str | None:
	doc = frappe.db.get_value(
		DOCUMENT,
		name,
		[
			"own_company",
			"thread_key",
			"thread_root",
			"mail_message_id",
			"mail_in_reply_to",
			"mail_references",
		],
		as_dict=True,
	)
	if not doc or not doc.thread_key:
		return None
	ids = _referenced(doc)
	or_filters = [["thread_key", "=", doc.thread_key]]
	if ids:
		or_filters.append(["mail_message_id", "in", ids])
	if doc.mail_message_id:
		or_filters.append(["mail_in_reply_to", "=", doc.mail_message_id])
	related = frappe.get_all(
		DOCUMENT,
		filters={"own_company": doc.own_company, "name": ("!=", name)},
		or_filters=or_filters,
		fields=["thread_key", "thread_root", "correspondence_at", "creation"],
		order_by="correspondence_at asc, creation asc",
	)
	roots = {row.thread_root or row.thread_key for row in related} | {doc.thread_root or doc.thread_key}
	root = (related[0].thread_root or related[0].thread_key) if related else doc.thread_root or doc.thread_key
	frappe.db.set_value(DOCUMENT, name, "thread_root", root, update_modified=False)
	others = list(roots - {root})
	if len(roots) > MAX_ROOTS:
		frappe.log_error(
			title="docarchive thread join",
			message=f"{name} refers to {len(roots)} conversations; only joined to the first.",
		)
		others = []
	if others:
		for field in ("thread_root", "thread_key"):
			filters = {"own_company": doc.own_company, field: ("in", others)}
			if field == "thread_key":
				filters["thread_root"] = None
			frappe.db.set_value(DOCUMENT, filters, "thread_root", root, update_modified=False)
	return root


def _referenced(mail):
	ids = [*(mail.mail_references or "").split(), mail.mail_in_reply_to]
	return [message_id for message_id in dict.fromkeys(ids) if message_id]


def _mail(mail):
	return {
		"kind": "mail",
		"name": mail.name,
		"title": mail.title,
		"channel": mail.correspondence_channel,
		"direction": mail.correspondence_direction,
		"at": mail.correspondence_at,
		"sender": mail.source_party_email,
	}


def _gap(message_id, own_company):
	gap = {"kind": "gap", "message_id": message_id, "communication": None, "business_document": None}
	document = _archived(message_id, own_company)
	if document:
		return {**gap, "state": "archived", "business_document": document}
	state, communication = _gap_state(message_id)
	return {**gap, "state": state, "communication": communication}


def _archived(message_id, own_company):
	names = frappe.get_list(
		DOCUMENT,
		filters={"mail_message_id": message_id, "own_company": own_company},
		pluck="name",
		order_by="correspondence_at desc, creation desc",
		limit=1,
	)
	return names[0] if names else None


def _gap_state(message_id):
	originals = frappe.get_all(
		ORIGINAL, filters={"message_id": message_id}, fields=["communication", "business_document"]
	)
	for original in originals:
		if (
			not original.business_document
			and original.communication
			and frappe.has_permission(COMMUNICATION, "read", original.communication)
		):
			return "captured", original.communication
	if not originals and _readable_communication(message_id):
		return "no_original", None
	return "unknown", None


def _readable_communication(message_id):
	return any(
		frappe.has_permission(COMMUNICATION, "read", name)
		for name in frappe.get_all(COMMUNICATION, filters={"message_id": message_id}, pluck="name")
	)


def follows(original_name: str, row) -> bool:
	if not cint(settings.get().get("follow_threads")):
		return False
	original = frappe.db.get_value(
		ORIGINAL,
		original_name,
		["thread_key", "message_id", "in_reply_to", "mail_references", "mail_at"],
		as_dict=True,
	)
	if not (original and original.thread_key and original.mail_at):
		return False
	try:
		own_company = settings.own_company_for(original.mail_at, row.own_company)
	except frappe.ValidationError:
		return False
	ids = _original_ids(original)
	related = frappe.get_all(
		DOCUMENT,
		filters={"own_company": own_company},
		or_filters=[
			["thread_key", "=", original.thread_key],
			["thread_root", "=", original.thread_key],
			["mail_message_id", "in", ids or [""]],
		],
		fields=["thread_root", "thread_key"],
		limit=MAX_MAILS,
	)
	roots = list({row.thread_root or row.thread_key for row in related})
	if not roots:
		return False
	earliest = frappe.get_all(
		DOCUMENT,
		filters={"own_company": own_company, "thread_root": ("in", roots)},
		fields=["correspondence_at"],
		order_by="correspondence_at asc",
		limit=1,
	)
	at = earliest[0].correspondence_at if earliest else None
	return bool(at) and get_datetime(at) <= get_datetime(original.mail_at)


@frappe.whitelist()
def thread_pending(communication: str) -> dict:
	empty = {"count": 0, "items": []}
	if communication not in access.readable([communication]):
		return empty
	original = frappe.db.get_value(
		ORIGINAL,
		{"communication": communication},
		[
			"name",
			"thread_key",
			"message_id",
			"in_reply_to",
			"mail_references",
			"mail_at",
			"business_document",
		],
		as_dict=True,
	)
	if not (original and original.thread_key):
		return empty
	keys, ids = {original.thread_key}, set(_original_ids(original))
	if original.business_document and frappe.has_permission(DOCUMENT, "read", original.business_document):
		start = frappe.db.get_value(
			DOCUMENT,
			original.business_document,
			[*FIELDS, "thread_key", "thread_root", "own_company"],
			as_dict=True,
		)
		for mail in _collect(start):
			keys.update(filter(None, (mail.thread_key, mail.thread_root)))
			ids.update(filter(None, (mail.mail_message_id, *_referenced(mail))))
	rows = frappe.get_all(
		ORIGINAL,
		filters={
			"status": "Stored",
			"business_document": None,
			"name": ("!=", original.name),
			"mail_at": ("<=", original.mail_at),
		},
		or_filters=[
			["thread_key", "in", list(keys)],
			["message_id", "in", list(ids) or [""]],
			["in_reply_to", "in", list(ids) or [""]],
		],
		fields=["name", "communication", "subject", "mail_at"],
		order_by="mail_at desc",
		limit=MAX_MAILS,
	)
	allowed = access.readable(row.communication for row in rows)
	items = [
		{"name": row.name, "communication": row.communication, "subject": row.subject, "date": row.mail_at}
		for row in rows
		if row.communication in allowed
	]
	return {"count": len(items), "items": items}


def _original_ids(original):
	ids = [*(original.mail_references or "").split(), original.in_reply_to, original.message_id]
	return [message_id for message_id in dict.fromkeys(ids) if message_id]
