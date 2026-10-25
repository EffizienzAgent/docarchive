import os
import time
from datetime import UTC
from email.utils import parsedate_to_datetime

import frappe
from frappe.email.doctype.email_queue.email_queue import SendMailContext
from frappe.utils import convert_utc_to_system_timezone, now_datetime

from docarchive import content
from docarchive.keys import slug
from docarchive.mail import accounts, archiving, headers

ORIGINAL = "Mail Original"
SAVEPOINT = "docarchive_mail_capture"
AUTO_SAVEPOINT = "docarchive_mail_auto_archive"
SEND_SAVEPOINT = "docarchive_mail_capture_sent"
STORE_SAVEPOINT = "docarchive_mail_store"
TRASH_SAVEPOINT = "docarchive_mail_release"
MAX_LENGTH = 500
SENT_FOLDER = "Sent Folder"
UPLOAD_ORIGIN = "manual-upload"
PENDING, STORED, FAILED = "Pending", "Stored", "Failed"
MAX_ATTEMPTS = 5
JOB = "docarchive.mail.originals.store"
SPOOL = ("private", "docarchive", "mail-spool")
ORPHAN_SECONDS = 86400
ERROR_LENGTH = 500


class Unavailable(Exception):
	pass


def capture(communication, raw: bytes | str, origin: str) -> str | None:
	try:
		frappe.db.savepoint(SAVEPOINT)
		existing = _existing(communication)
		if existing:
			return _revive(existing, _as_bytes(raw))
		name = _pend(communication, origin, raw=_as_bytes(raw))
	except Exception:
		fail(SAVEPOINT)
		return None
	if name:
		schedule(name)
	return name


def _revive(name, raw):
	if frappe.db.get_value(ORIGINAL, name, "status") == FAILED:
		write_spool(name, raw)
		frappe.db.set_value(
			ORIGINAL,
			name,
			{"status": PENDING, "attempts": 0, "error": None, "email_queue": None},
			update_modified=False,
		)
		schedule(name)
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
		row = accounts.account_settings(communication.email_account)
		if _existing(communication) or not row or row.outgoing_source == SENT_FOLDER:
			return None
		name = _pend(communication, "frappe-send", email_queue=queue.name)
	except Exception:
		fail(SEND_SAVEPOINT)
		return None
	if name:
		schedule(name)
		_commit()
	return name


def schedule(name: str) -> None:
	frappe.db.after_commit.add(lambda: enqueue(name))


def enqueue(name: str) -> None:
	try:
		frappe.enqueue(
			JOB,
			queue="long",
			job_id=f"docarchive-mail-original-{name}",
			deduplicate=True,
			original=name,
		)
	except Exception:
		fail()


def store(original: str) -> None:
	name = original
	if frappe.db.get_value(ORIGINAL, name, "status") != PENDING:
		return
	try:
		frappe.db.savepoint(STORE_SAVEPOINT)
		original = frappe.get_doc(ORIGINAL, name)
		_complete(original, _rebuilt(original) if original.email_queue else read_spool(name))
	except Exception as error:
		fail(STORE_SAVEPOINT)
		_attempted(name, error)
		return
	frappe.db.after_commit.add(lambda: remove_spool(name))
	_auto_archive(name)


def retry_pending() -> int:
	names = frappe.get_all(ORIGINAL, filters={"status": PENDING}, pluck="name")
	for name in names:
		enqueue(name)
	return len(names)


def retry_failed() -> int:
	names = frappe.get_all(ORIGINAL, filters={"status": FAILED}, pluck="name")
	for name in names:
		frappe.db.set_value(ORIGINAL, name, {"status": PENDING, "attempts": 0}, update_modified=False)
		schedule(name)
	return len(names)


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


def _rebuilt(original):
	if not frappe.db.exists("Email Queue", original.email_queue):
		raise Unavailable(f"Email Queue {original.email_queue} no longer exists.")
	queue = frappe.get_doc("Email Queue", original.email_queue)
	if not _capturable(queue):
		raise Unavailable(f"Email Queue {queue.name} cannot be rebuilt.")
	recipient = next((row.recipient for row in queue.recipients if row.is_mail_sent()), None)
	if not recipient:
		raise Unavailable(f"Email Queue {queue.name} has no sent recipient.")
	ctx = SendMailContext(queue)
	ctx.email_account_doc = queue.get_email_account(raise_error=True)
	return _as_bytes(ctx.build_message(recipient))


def _attempted(name, error):
	try:
		attempts = (frappe.db.get_value(ORIGINAL, name, "attempts") or 0) + 1
		status = FAILED if isinstance(error, Unavailable) or attempts >= MAX_ATTEMPTS else PENDING
		frappe.db.set_value(
			ORIGINAL,
			name,
			{
				"attempts": attempts,
				"status": status,
				"error": (str(error) or type(error).__name__)[:ERROR_LENGTH],
			},
			update_modified=False,
		)
	except Exception:
		fail()


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
			ORIGINAL, {"communication": communication}, "communication", None, update_modified=False
		)
	except Exception:
		fail(TRASH_SAVEPOINT)


def _release(communication):
	original = frappe.db.get_value(
		ORIGINAL, {"communication": communication}, ["name", "business_document"], as_dict=True
	)
	if not original:
		return
	if original.business_document:
		frappe.db.set_value(ORIGINAL, original.name, "communication", None, update_modified=False)
	else:
		frappe.delete_doc(ORIGINAL, original.name, ignore_permissions=True)


def _as_bytes(raw):
	return raw.encode("utf-8", "surrogateescape") if isinstance(raw, str) else raw


def _existing(communication):
	return frappe.db.get_value(ORIGINAL, {"communication": communication.name})


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


def spool_path(name: str) -> str:
	return frappe.get_site_path(*SPOOL, f"{name}.eml")


def write_spool(name: str, raw: bytes) -> None:
	path = spool_path(name)
	folder = os.path.dirname(path)
	os.makedirs(folder, mode=0o700, exist_ok=True)
	os.chmod(folder, 0o700)
	descriptor = os.open(f"{path}.tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
	with os.fdopen(descriptor, "wb") as f:
		f.write(raw)
	os.replace(f"{path}.tmp", path)


def read_spool(name: str) -> bytes:
	try:
		with open(spool_path(name), "rb") as f:
			return f.read()
	except FileNotFoundError:
		raise FileNotFoundError(f"The spool file of Mail Original {name} is missing.") from None


def remove_spool(name: str) -> None:
	try:
		os.remove(spool_path(name))
	except FileNotFoundError:
		pass
	except OSError:
		fail()


def sweep_spool() -> int:
	folder = frappe.get_site_path(*SPOOL)
	if not os.path.isdir(folder):
		return 0
	waiting = set(frappe.get_all(ORIGINAL, filters={"status": ("in", (PENDING, FAILED))}, pluck="name"))
	removed = 0
	for entry in os.scandir(folder):
		try:
			name = entry.name.removesuffix(".tmp").removesuffix(".eml")
			if (
				entry.is_file(follow_symlinks=False)
				and name not in waiting
				and time.time() - entry.stat().st_mtime > ORPHAN_SECONDS
			):
				os.remove(entry.path)
				removed += 1
		except OSError:
			fail()
	return removed


def store_file(original, raw: bytes) -> str:
	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": slug(original.message_id or original.communication or original.name) + ".eml",
			"content": raw,
			"is_private": 1,
			"attached_to_doctype": ORIGINAL,
			"attached_to_name": original.name,
		}
	).insert(ignore_permissions=True)
	return file_doc.file_url


def _pend(communication, origin, raw=None, email_queue=None):
	if not accounts.account_settings(communication.email_account):
		return None
	original = frappe.get_doc(
		{
			"doctype": ORIGINAL,
			"communication": communication.name,
			"email_account": communication.email_account,
			"origin": origin,
			"direction": "Outgoing" if communication.sent_or_received == "Sent" else "Incoming",
			"mail_at": communication.communication_date,
			"status": PENDING,
			"email_queue": email_queue,
		}
	).insert(ignore_permissions=True)
	if raw is not None:
		write_spool(original.name, raw)
	return original.name


def _complete(original, raw):
	parsed = headers.parse(raw)
	row = accounts.account_row(original.email_account)
	internal = bool(row) and headers.is_internal(parsed, accounts.internal_domains(row))
	original.update(
		{**_header_fields(raw, parsed), "internal": int(internal), "status": STORED, "error": None}
	)
	original.save(ignore_permissions=True)
	original.db_set("file", store_file(original, raw))


def create_upload(raw: bytes, parsed, direction: str) -> str:
	original = frappe.get_doc(
		{
			"doctype": ORIGINAL,
			**_header_fields(raw, parsed),
			"origin": UPLOAD_ORIGIN,
			"direction": direction,
			"mail_at": mail_time(parsed),
			"internal": 0,
			"status": STORED,
		}
	).insert(ignore_permissions=True)
	original.db_set("file", store_file(original, raw))
	return original.name


def mail_time(parsed):
	try:
		stamp = parsedate_to_datetime(parsed.date_header)
	except TypeError, ValueError:
		return now_datetime()
	if stamp.tzinfo is None:
		return stamp
	utc = stamp.astimezone(UTC).replace(tzinfo=None)
	return convert_utc_to_system_timezone(utc).replace(tzinfo=None)


def _header_fields(raw, parsed):
	digest = content.sha256(raw)
	return {
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
	}


def _auto_archive(name):
	try:
		frappe.db.savepoint(AUTO_SAVEPOINT)
		archiving.auto_archive(name)
	except Exception:
		fail(AUTO_SAVEPOINT)
