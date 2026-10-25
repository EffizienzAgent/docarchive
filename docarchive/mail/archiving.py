import frappe
from frappe import _
from frappe.utils import getdate

from docarchive import content, counterparty, settings
from docarchive.document_types import CORRESPONDENCE, INTERNAL_CORRESPONDENCE
from docarchive.mail import accounts

DOCUMENT = "Business Document"
ORIGINAL = "Mail Original"
COMMUNICATION = "Communication"
SAVEPOINT = "docarchive_mail_archive"
TITLE_LENGTH = 140
EMAIL_LENGTH = 140


def archive(original_name: str) -> str:
	original = frappe.get_doc(ORIGINAL, original_name)
	if original.business_document:
		return original.business_document
	row = accounts.account_row(original.email_account) or frappe._dict()
	own_company = settings.own_company_for(original.mail_at, row.own_company)
	name = _existing(original, own_company) or _create(original, own_company)
	original.db_set("business_document", name, update_modified=False)
	return name


def refusal(original_name: str | None) -> str | None:
	return None if original_name else _("No original was captured for this e-mail.")


def auto_archive(original_name: str) -> str | None:
	original = frappe.db.get_value(
		ORIGINAL, original_name, ["email_account", "automated", "internal"], as_dict=True
	)
	row = accounts.account_settings(original.email_account)
	if (
		not row
		or row.mode != "Automatic"
		or original.automated
		or (original.internal and not row.archive_internal)
	):
		return None
	return archive(original_name)


def remove_document(name: str) -> None:
	if frappe.db.get_value(DOCUMENT, name, "archive_started_at"):
		frappe.throw(_("Archived documents cannot be discarded."))
	for original in frappe.get_all(ORIGINAL, filters={"business_document": name}, pluck="name"):
		frappe.delete_doc(ORIGINAL, original, ignore_permissions=True)
	frappe.delete_doc(DOCUMENT, name, ignore_permissions=True)


def assign(doc):
	if doc.get("source_doctype") != COMMUNICATION or not doc.get("source_name") or doc.get("counterparty"):
		return
	candidates = _candidates(doc)
	if len(candidates) == 1:
		doc.counterparty_type, doc.counterparty = candidates[0]


@frappe.whitelist()
def status(communication: str) -> dict:
	frappe.has_permission(COMMUNICATION, "read", communication, throw=True)
	original = (
		frappe.db.get_value(
			ORIGINAL,
			{"communication": communication},
			["name", "business_document", "automated", "internal"],
			as_dict=True,
		)
		or frappe._dict()
	)
	return {
		"original": bool(original.name),
		"business_document": original.business_document or None,
		"automated": bool(original.automated),
		"internal": bool(original.internal),
		"reason": refusal(original.name),
	}


@frappe.whitelist(methods=["POST"])
def archive_communications(names) -> dict:
	names = _names(names)
	frappe.has_permission(DOCUMENT, "create", throw=True)
	for name in names:
		frappe.has_permission(COMMUNICATION, "read", name, throw=True)
	result = {"archived": {}, "refused": {}}
	for name in names:
		original = frappe.db.get_value(ORIGINAL, {"communication": name})
		reason = refusal(original)
		if reason:
			result["refused"][name] = reason
			continue
		try:
			frappe.db.savepoint(SAVEPOINT)
			result["archived"][name] = archive(original)
		except frappe.ValidationError as error:
			frappe.db.rollback(save_point=SAVEPOINT)
			result["refused"][name] = str(error)
	return result


@frappe.whitelist(methods=["POST"])
def discard(names) -> dict:
	names = _names(names)
	for name in names:
		frappe.has_permission(DOCUMENT, "write", name, throw=True)
	result = {"discarded": [], "refused": {}}
	for name in names:
		reason = _discard_refusal(name)
		if reason:
			result["refused"][name] = reason
			continue
		remove_document(name)
		result["discarded"].append(name)
	return result


def _names(names) -> list[str]:
	return list(frappe.parse_json(names) if isinstance(names, str) else names or [])


def _discard_refusal(name):
	source, started = frappe.db.get_value(DOCUMENT, name, ["source_doctype", "archive_started_at"])
	if started:
		return _("Archived documents cannot be discarded.")
	if source != COMMUNICATION:
		return _("Only e-mail documents can be discarded.")
	return None


def _existing(original, own_company):
	filters = {"own_company": own_company}
	if original.message_id:
		filters["mail_message_id"] = original.message_id
	else:
		filters.update(content_sha256=original.sha256, source_doctype=COMMUNICATION)
	return frappe.db.get_value(DOCUMENT, filters)


def _create(original, own_company):
	doc = frappe.get_doc(
		{
			"doctype": DOCUMENT,
			"document_type": INTERNAL_CORRESPONDENCE if original.internal else CORRESPONDENCE,
			"own_company": own_company,
			"document_date": getdate(original.mail_at),
			"title": (original.subject or _("(no subject)"))[:TITLE_LENGTH],
			"source_system": "email",
			"source_account": original.email_account,
			"source_doctype": COMMUNICATION,
			"source_name": original.communication,
			"source_party_email": _party_email(original),
			"correspondence_channel": "E-mail",
			"correspondence_direction": original.direction,
			"correspondence_at": original.mail_at,
			"thread_key": original.thread_key,
			"mail_message_id": original.message_id,
			"mail_in_reply_to": original.in_reply_to,
			"mail_references": original.mail_references,
		}
	)
	doc.flags.archive_write = True
	doc.insert(ignore_permissions=True)
	doc.file = _attach(original, doc.name)
	doc.save(ignore_permissions=True)
	return doc.name


def _party_email(original):
	value = original.sender if original.direction == "Incoming" else original.to
	return (value or "").split(", ")[0][:EMAIL_LENGTH]


def _attach(original, document):
	data = content.read_bytes(original.file)
	if data is None:
		frappe.throw(_("The captured original of this e-mail is missing."))
	file_name = frappe.db.get_value(
		"File",
		{"file_url": original.file, "attached_to_doctype": ORIGINAL, "attached_to_name": original.name},
		"file_name",
	)
	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": file_name or f"{original.name}.eml",
			"content": data,
			"is_private": 1,
			"attached_to_doctype": DOCUMENT,
			"attached_to_name": document,
			"attached_to_field": "file",
		}
	).insert(ignore_permissions=True)
	return file_doc.file_url


def _candidates(doc):
	row = accounts.account_row(doc.get("source_account")) or frappe._dict(
		email_account=doc.get("source_account")
	)
	domains = accounts.internal_domains(row)
	found = []
	for link in frappe.get_all(
		"Communication Link",
		filters={"parenttype": COMMUNICATION, "parent": doc.source_name},
		fields=["link_doctype", "link_name"],
		order_by="idx",
	):
		if link.link_doctype == "Contact":
			found.extend(_contact_parties(link.link_name, domains, row.contacts_as_counterparty))
		else:
			found.append((link.link_doctype, link.link_name))
	allowed = counterparty.doctypes()
	return [party for party in dict.fromkeys(found) if not allowed or party[0] in allowed]


def _contact_parties(contact, domains, contacts_count):
	if _internal_contact(contact, domains):
		return []
	links = frappe.get_all(
		"Dynamic Link",
		filters={"parenttype": "Contact", "parentfield": "links", "parent": contact},
		fields=["link_doctype", "link_name"],
		order_by="idx",
	)
	if links:
		return [(link.link_doctype, link.link_name) for link in links]
	return [("Contact", contact)] if contacts_count else []


def _internal_contact(contact, domains):
	emails = frappe.get_all(
		"Contact Email", filters={"parenttype": "Contact", "parent": contact}, pluck="email_id"
	)
	return bool(emails) and all(email.rpartition("@")[2].lower() in domains for email in emails)
