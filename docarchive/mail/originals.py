import frappe

from docarchive import content
from docarchive.keys import slug
from docarchive.mail import accounts, archiving, headers

SAVEPOINT = "docarchive_mail_capture"


def capture(communication, raw: bytes | str, origin: str) -> str | None:
	if isinstance(raw, str):
		raw = raw.encode("utf-8", "surrogateescape")
	frappe.db.savepoint(SAVEPOINT)
	try:
		name = _capture(communication, raw, origin)
	except Exception:
		frappe.db.rollback(save_point=SAVEPOINT)
		frappe.log_error(title="docarchive mail capture")
		return None
	if name:
		_auto_archive(name)
	return name


def store_file(original, raw: bytes) -> str:
	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": slug(original.message_id or original.communication) + ".eml",
			"content": raw,
			"is_private": 1,
			"attached_to_doctype": "Mail Original",
			"attached_to_name": original.name,
		}
	).insert(ignore_permissions=True)
	return file_doc.file_url


def _capture(communication, raw, origin):
	existing = frappe.db.get_value("Mail Original", {"communication": communication.name})
	if existing:
		return existing
	row = accounts.account_settings(communication.email_account)
	if not row:
		return None
	digest = content.sha256(raw)
	parsed = headers.parse(raw)
	original = frappe.get_doc(
		{
			"doctype": "Mail Original",
			"communication": communication.name,
			"email_account": communication.email_account,
			"origin": origin,
			"sha256": digest,
			"size": len(raw),
			"direction": "Outgoing" if communication.sent_or_received == "Sent" else "Incoming",
			"message_id": parsed.message_id,
			"in_reply_to": parsed.in_reply_to,
			"mail_references": " ".join(parsed.references),
			"thread_key": headers.thread_key(parsed, digest),
			"sender": parsed.sender,
			"to": parsed.to,
			"cc": parsed.cc,
			"subject": parsed.subject,
			"date_header": parsed.date_header,
			"mail_at": communication.communication_date,
			"automated": int(parsed.automated),
			"internal": int(headers.is_internal(parsed, accounts.internal_domains(row))),
		}
	).insert(ignore_permissions=True)
	original.db_set("file", store_file(original, raw))
	return original.name


def _auto_archive(name):
	try:
		archiving.auto_archive(name)
	except Exception:
		frappe.log_error(title="docarchive mail capture")
