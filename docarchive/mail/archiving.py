import frappe
from frappe import _
from frappe.utils import get_datetime, getdate

from docarchive import content, counterparty, settings
from docarchive.document_types import CORRESPONDENCE, INTERNAL_CORRESPONDENCE
from docarchive.mail import accounts, headers

DOCUMENT = "Business Document"
ORIGINAL = "Mail Original"
COMMUNICATION = "Communication"
DIRECTIONS = ("Incoming", "Outgoing")
SAVEPOINT = "docarchive_mail_archive"
SEND_NOTE = "Copies to other recipients differ only in tracking and unsubscribe links."
TITLE_LENGTH = 140
EMAIL_LENGTH = 140


def archive(original_name: str, own_company: str | None = None, check_permission: bool = False) -> str:
	original = frappe.get_doc(ORIGINAL, original_name)
	if original.business_document:
		return original.business_document
	reason = refusal(original_name)
	if reason:
		frappe.throw(reason)
	row = accounts.account_row(original.email_account) or frappe._dict()
	own_company = settings.own_company_for(original.mail_at, own_company or row.own_company)
	name = _existing(original, own_company) or _create(original, own_company, check_permission)
	original.db_set("business_document", name, update_modified=False)
	return name


def refusal(original_name: str | None) -> str | None:
	if not original_name:
		return _("No original was captured for this e-mail.")
	status = frappe.db.get_value(ORIGINAL, original_name, "status")
	if status == "Pending":
		return _("The original of this e-mail is still being stored. Please try again in a moment.")
	if status == "Failed":
		return _("Storing the original of this e-mail failed. See the Error Log.")
	return None


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


def mail_meta(doc) -> dict | None:
	original = _meta_original(doc)
	if not original:
		return None
	meta = {
		"message_id": original.message_id,
		"in_reply_to": original.in_reply_to,
		"references": (original.mail_references or "").split(),
		"from": original.sender,
		"to": original.to,
		"cc": original.cc,
		"subject": original.subject,
		"date_header": original.date_header,
		"account": original.email_account,
		"origin": original.origin,
	}
	if original.origin == "frappe-send":
		meta["note"] = SEND_NOTE
	if original.rebuilt_at:
		meta["rebuilt_at"] = get_datetime(original.rebuilt_at).isoformat() + "Z"
	return meta


def _meta_original(doc):
	originals = frappe.get_all(
		ORIGINAL,
		filters={"business_document": doc.name},
		fields=[
			"message_id",
			"in_reply_to",
			"mail_references",
			"sender",
			"to",
			"cc",
			"subject",
			"date_header",
			"email_account",
			"origin",
			"sha256",
			"rebuilt_at",
		],
		order_by="creation asc, name asc",
	)
	match = next((row for row in originals if row.sha256 == doc.get("content_sha256")), None)
	return match or (originals[0] if originals else None)


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
	document = original.business_document
	return {
		"original": bool(original.name),
		"archived": bool(document),
		"business_document": document
		if document and frappe.has_permission(DOCUMENT, "read", document)
		else None,
		"automated": bool(original.automated),
		"internal": bool(original.internal),
		"reason": refusal(original.name),
	}


@frappe.whitelist()
def warnings(names) -> dict:
	names = _names(names)
	for name in names:
		frappe.has_permission(COMMUNICATION, "read", name, throw=True)
	rows = frappe.get_all(
		ORIGINAL,
		filters={"communication": ("in", names or [""]), "business_document": ("is", "not set")},
		fields=["communication", "automated", "internal"],
	)
	order = {name: index for index, name in enumerate(names)}
	rows.sort(key=lambda row: order[row.communication])
	return {
		"automated": [row.communication for row in rows if row.automated],
		"internal": [row.communication for row in rows if row.internal and not row.automated],
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
		messages = len(frappe.local.message_log)
		try:
			frappe.db.savepoint(SAVEPOINT)
			result["archived"][name] = archive(original, check_permission=True)
		except (frappe.ValidationError, frappe.PermissionError) as error:
			frappe.db.rollback(save_point=SAVEPOINT)
			del frappe.local.message_log[messages:]
			result["refused"][name] = str(error)
	return result


@frappe.whitelist(methods=["POST"])
def archive_upload(
	file_url: str,
	direction: str,
	gap_message_id: str | None = None,
	own_company: str | None = None,
	counterparty_type: str | None = None,
	counterparty: str | None = None,
	source_doctype: str | None = None,
	source_name: str | None = None,
) -> str:
	from docarchive.documents import check_source
	from docarchive.mail import originals

	frappe.has_permission(DOCUMENT, "create", throw=True)
	if counterparty_type and counterparty:
		frappe.has_permission(counterparty_type, "read", counterparty, throw=True)
	check_source(source_doctype, source_name)
	if direction not in DIRECTIONS:
		frappe.throw(_("The direction must be Incoming or Outgoing."))
	file_name = _upload_name(file_url)
	raw = content.read_bytes(file_url)
	if raw is None:
		frappe.throw(_("File {0} does not exist.").format(frappe.utils.escape_html(file_url)))
	parsed = headers.parse(raw)
	if not (parsed.sender or parsed.message_id or parsed.date_header):
		frappe.throw(_("This file is not an e-mail."))
	if gap_message_id and parsed.message_id != gap_message_id:
		frappe.throw(
			_("This e-mail is not the missing one: its Message-ID is {0}.").format(
				frappe.utils.escape_html(parsed.message_id or "-")
			)
		)
	thread = _gap_thread(gap_message_id) if gap_message_id and not own_company else frappe._dict()
	company = settings.own_company_for(originals.mail_time(parsed), own_company or thread.own_company)
	name = _existing_document(parsed.message_id, content.sha256(raw), company)
	if not name:
		original = originals.create_upload(raw, parsed, direction)
		name = archive(original, company, check_permission=True)
		party = frappe._dict(counterparty_type=counterparty_type, counterparty=counterparty)
		_complete(name, party if counterparty_type and counterparty else thread, source_doctype, source_name)
	frappe.delete_doc("File", file_name, ignore_permissions=True)
	return name


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
	source, system, started, hold = frappe.db.get_value(
		DOCUMENT, name, ["source_doctype", "source_system", "archive_started_at", "hold_on_archive"]
	)
	if started:
		return _("Archived documents cannot be discarded.")
	if hold:
		return _("Documents marked for legal hold cannot be discarded.")
	if source != COMMUNICATION and system != "email":
		return _("Only e-mail documents can be discarded.")
	return None


def _gap_thread(message_id):
	rows = frappe.get_list(
		DOCUMENT,
		or_filters=[["mail_references", "like", f"%{message_id}%"], ["mail_in_reply_to", "=", message_id]],
		fields=["own_company", "counterparty_type", "counterparty", "mail_references", "mail_in_reply_to"],
		order_by="correspondence_at asc",
		limit_page_length=50,
	)
	for row in rows:
		if message_id == row.mail_in_reply_to or message_id in (row.mail_references or "").split():
			return row
	return frappe._dict()


def _complete(name, party, source_doctype, source_name):
	if not (party.counterparty or source_doctype):
		return
	doc = frappe.get_doc(DOCUMENT, name)
	if party.counterparty:
		doc.counterparty_type, doc.counterparty = party.counterparty_type, party.counterparty
	if source_doctype:
		doc.source_doctype, doc.source_name = source_doctype, source_name
		doc.flags.archive_write = True
	doc.save(ignore_permissions=True)


def _upload_name(file_url):
	name = frappe.db.get_value(
		"File",
		{
			"file_url": file_url,
			"owner": frappe.session.user,
			"is_private": 1,
			"attached_to_doctype": ("is", "not set"),
		},
		"name",
	)
	if not name or not file_url.lower().endswith(".eml"):
		frappe.throw(_("Please upload the e-mail as a private .eml file."))
	return name


def _existing(original, own_company):
	return _existing_document(original.message_id, original.sha256, own_company)


def _existing_document(message_id, sha256, own_company):
	filters = {"own_company": own_company}
	if message_id:
		filters["mail_message_id"] = message_id
	else:
		filters.update(content_sha256=sha256, source_system="email")
	return frappe.db.get_value(DOCUMENT, filters)


def _create(original, own_company, check_permission=False):
	doc = frappe.get_doc(
		{
			"doctype": DOCUMENT,
			"document_type": INTERNAL_CORRESPONDENCE if original.internal else CORRESPONDENCE,
			"own_company": own_company,
			"document_date": getdate(original.mail_at),
			"title": (original.subject or _("(no subject)"))[:TITLE_LENGTH],
			"source_system": "email",
			"source_account": original.email_account,
			"source_doctype": COMMUNICATION if original.email_account else None,
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
	if check_permission:
		frappe.has_permission(DOCUMENT, "create", doc=doc, throw=True)
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
