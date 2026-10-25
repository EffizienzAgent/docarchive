from datetime import UTC
from email.utils import parsedate_to_datetime

import frappe
from frappe.email.doctype.email_queue.email_queue import SendMailContext
from frappe.utils import convert_utc_to_system_timezone, now_datetime

from docarchive import content
from docarchive.keys import slug
from docarchive.mail import accounts, archiving, headers

SAVEPOINT = "docarchive_mail_capture"
AUTO_SAVEPOINT = "docarchive_mail_auto_archive"
SEND_SAVEPOINT = "docarchive_mail_capture_sent"
TRASH_SAVEPOINT = "docarchive_mail_release"
MAX_LENGTH = 500
SENT_FOLDER = "Sent Folder"
UPLOAD_ORIGIN = "manual-upload"


def capture(communication, raw: bytes | str, origin: str) -> str | None:
	try:
		frappe.db.savepoint(SAVEPOINT)
		existing = _existing(communication)
		if existing:
			return existing
		name = _capture(communication, _as_bytes(raw), origin)
	except Exception:
		fail(SAVEPOINT)
		return None
	if name:
		_auto_archive(name)
	return name


def from_mailsync(communication, raw, account=None, folder=None, sent_or_received=None, **kwargs) -> None:
	try:
		if isinstance(communication, str):
			communication = frappe.get_doc("Communication", communication)
		capture(communication, raw, "mailsync")
	except Exception:
		fail()


def sent_count(queue) -> int | None:
	try:
		if not _capturable(queue):
			return None
		return frappe.db.count("Email Queue Recipient", {"parent": queue.name, "status": "Sent"})
	except Exception:
		fail()
		return None


def capture_sent(queue, sent_before: int | None) -> str | None:
	try:
		if sent_before is None or (sent_count(queue) or 0) <= sent_before:
			return None
		frappe.db.savepoint(SEND_SAVEPOINT)
		communication = frappe.get_doc("Communication", queue.communication)
		raw = _sent_bytes(queue, communication)
	except Exception:
		fail(SEND_SAVEPOINT)
		return None
	name = capture(communication, raw, "frappe-send") if raw else None
	if name:
		_commit()
	return name


def _commit():
	try:
		frappe.db.commit()
	except Exception:
		fail()


def _capturable(queue):
	return (
		queue.get("communication")
		and queue.get("reference_doctype") != "Newsletter"
		and not queue.get("redact_message_after_send")
	)


def _sent_bytes(queue, communication):
	row = accounts.account_settings(communication.email_account)
	if _existing(communication) or not row or row.outgoing_source == SENT_FOLDER:
		return None
	queue.reload()
	recipient = next((row.recipient for row in queue.recipients if row.is_mail_sent()), None)
	if not recipient:
		return None
	ctx = SendMailContext(queue)
	ctx.email_account_doc = queue.get_email_account(raise_error=True)
	return ctx.build_message(recipient)


def on_communication_trash(communication, method=None):
	try:
		frappe.db.savepoint(TRASH_SAVEPOINT)
		_release(communication.name)
	except Exception:
		fail(TRASH_SAVEPOINT)
		_unlink(communication.name)


def _unlink(communication):
	try:
		frappe.db.savepoint(TRASH_SAVEPOINT)
		frappe.db.set_value(
			"Mail Original", {"communication": communication}, "communication", None, update_modified=False
		)
	except Exception:
		fail(TRASH_SAVEPOINT)


def _release(communication):
	original = frappe.db.get_value(
		"Mail Original", {"communication": communication}, ["name", "business_document"], as_dict=True
	)
	if not original:
		return
	if original.business_document:
		frappe.db.set_value("Mail Original", original.name, "communication", None, update_modified=False)
	else:
		frappe.delete_doc("Mail Original", original.name, ignore_permissions=True)


def _as_bytes(raw):
	return raw.encode("utf-8", "surrogateescape") if isinstance(raw, str) else raw


def _existing(communication):
	return frappe.db.get_value("Mail Original", {"communication": communication.name})


def fail(savepoint=None):
	try:
		if savepoint:
			frappe.db.rollback(save_point=savepoint)
	except Exception:
		pass
	try:
		frappe.log_error(title="docarchive mail capture")
	except Exception:
		pass


def store_file(original, raw: bytes) -> str:
	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": slug(original.message_id or original.communication or original.name) + ".eml",
			"content": raw,
			"is_private": 1,
			"attached_to_doctype": "Mail Original",
			"attached_to_name": original.name,
		}
	).insert(ignore_permissions=True)
	return file_doc.file_url


def _capture(communication, raw, origin):
	row = accounts.account_settings(communication.email_account)
	if not row:
		return None
	parsed = headers.parse(raw)
	return _insert(
		raw,
		parsed,
		communication=communication.name,
		email_account=communication.email_account,
		origin=origin,
		direction="Outgoing" if communication.sent_or_received == "Sent" else "Incoming",
		mail_at=communication.communication_date,
		internal=int(headers.is_internal(parsed, accounts.internal_domains(row))),
	)


def create_upload(raw: bytes, parsed, direction: str) -> str:
	return _insert(
		raw, parsed, origin=UPLOAD_ORIGIN, direction=direction, mail_at=mail_time(parsed), internal=0
	)


def mail_time(parsed):
	try:
		stamp = parsedate_to_datetime(parsed.date_header)
	except TypeError, ValueError:
		return now_datetime()
	if stamp.tzinfo is None:
		return stamp
	utc = stamp.astimezone(UTC).replace(tzinfo=None)
	return convert_utc_to_system_timezone(utc).replace(tzinfo=None)


def _insert(raw, parsed, **fields):
	digest = content.sha256(raw)
	original = frappe.get_doc(
		{
			"doctype": "Mail Original",
			"sha256": digest,
			"size": len(raw),
			"message_id": parsed.message_id[:MAX_LENGTH],
			"in_reply_to": parsed.in_reply_to[:MAX_LENGTH],
			"mail_references": " ".join(parsed.references),
			"thread_key": headers.thread_key(parsed, digest)[:MAX_LENGTH],
			"sender": parsed.sender[:MAX_LENGTH],
			"to": parsed.to,
			"cc": parsed.cc,
			"subject": parsed.subject,
			"date_header": parsed.date_header,
			"automated": int(parsed.automated),
			**fields,
		}
	).insert(ignore_permissions=True)
	original.db_set("file", store_file(original, raw))
	return original.name


def _auto_archive(name):
	try:
		frappe.db.savepoint(AUTO_SAVEPOINT)
		archiving.auto_archive(name)
	except Exception:
		fail(AUTO_SAVEPOINT)
