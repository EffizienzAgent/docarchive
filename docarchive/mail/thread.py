import frappe

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


@frappe.whitelist()
def thread(name: str) -> list[dict]:
	frappe.has_permission(DOCUMENT, "read", name, throw=True)
	key, own_company = frappe.db.get_value(DOCUMENT, name, ["thread_key", "own_company"])
	if not key:
		return []
	mails = frappe.get_list(
		DOCUMENT,
		filters={"thread_key": key, "own_company": own_company},
		fields=FIELDS,
		order_by="correspondence_at desc, creation desc",
	)
	seen = {mail.mail_message_id for mail in mails if mail.mail_message_id}
	items = []
	for mail in reversed(mails):
		for message_id in _referenced(mail):
			if message_id not in seen:
				seen.add(message_id)
				items.append(_gap(message_id, own_company))
		items.append(_mail(mail))
	return items[::-1]


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
