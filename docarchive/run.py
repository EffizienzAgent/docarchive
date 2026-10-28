import json
import mimetypes
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import frappe
from frappe import _
from frappe.utils import add_to_date, get_system_timezone, now_datetime

from docarchive import assignment, audit, content, keys, retention, settings, storage_targets, store
from docarchive.document_archive.doctype.business_document.business_document import (
	extension,
	file_name,
	missing_fields,
	own_file,
)
from docarchive.mail import archiving

DOCUMENT = "Business Document"
SAVEPOINT = "docarchive_save"
LOCK_SECONDS = 1800
PENDING, ARCHIVED, FAILED, SKIPPED = "Pending", "Archived", "Failed", "Skipped"
MAIN_ROLE = "Main"
DELETED = "Deleted"
CANDIDATE_FIELDS = [
	"name",
	"document_type",
	"document_date",
	"own_company",
	"einvoice_needs_review",
	"file",
	"counterparty",
	"document_number",
	"contract_end_date",
	"archive_started_at",
	"correspondence_channel",
	"correspondence_direction",
]


class ArchiveError(frappe.ValidationError):
	def __init__(self, message, code="archive"):
		super().__init__(message)
		self.code = code


def schedule():
	frappe.enqueue("docarchive.run.nightly", queue="long", job_id="docarchive-nightly", deduplicate=True)


def nightly(dry_run=False) -> dict:
	if dry_run:
		return run_nightly(dry_run)
	from docarchive import monitoring

	try:
		summary = run_nightly()
	except Exception as e:
		frappe.db.rollback()
		monitoring.run_failed(safe_error(e))
		frappe.db.commit()
		raise
	if summary["status"] == "done":
		try:
			monitoring.after_run(summary)
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title="docarchive: monitoring")
	return summary


def run_nightly(dry_run=False) -> dict:
	config = settings.get()
	if not (config.enabled or dry_run):
		return {"status": "disabled"}
	targets = store.targets()
	if not targets:
		return {"status": "not configured"}
	if dry_run:
		return {
			"status": "dry run",
			"targets": [t.name for t in targets],
			"documents": [
				{"name": name, "object_key": planned_key(name)}
				for name in eligible({t.name for t in targets})
			],
			"extensions": [
				{"name": doc.name, "retain_until": until.isoformat()}
				for doc, until in extensions({t.name for t in targets})
			],
		}
	reassign()
	results, outcomes = {ARCHIVED: [], FAILED: [], SKIPPED: []}, {}
	with audit.batch(audit.ARCHIVE_RUN) as documents:
		for name in eligible({t.name for t in targets}):
			documents.start(DOCUMENT, name)
			results[archive_document(name, targets, outcomes)].append(name)
			documents.done(DOCUMENT, name, *outcomes[name])
		extended = extend_retention(targets, documents)
	summary = {
		"status": "done",
		"archived": len(results[ARCHIVED]),
		"failed": results[FAILED],
		"extended": len(extended[ARCHIVED]),
		"extension_failed": extended[FAILED],
		"skipped": results[SKIPPED],
		"audit": audit_nightly(targets),
	}
	frappe.logger("docarchive").info(summary)
	return summary


def audit_nightly(targets) -> dict:
	try:
		return audit.nightly(targets)
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title="docarchive: audit nightly")
		return {"ok": False, "error": safe_error(e)}


def reason_code(error) -> str:
	code = store.error_code(error)
	return f"storage:{code}" if code else getattr(error, "code", None) or type(error).__name__


def archive_types() -> list[str]:
	return frappe.get_all("Business Document Type", filters={"archive": 1}, pluck="name")


def reassign():
	types = archive_types()
	if not types:
		return
	for name in frappe.get_all(
		DOCUMENT,
		filters={
			"document_type": ("in", types),
			"archive_started_at": ("is", "not set"),
			"counterparty": ("is", "not set"),
		},
		or_filters={"source_system": ("is", "set"), "einvoice_data": ("is", "set")},
		pluck="name",
	):
		frappe.db.savepoint(SAVEPOINT)
		try:
			doc = frappe.get_doc(DOCUMENT, name)
			if assignment.assign(doc) and doc.counterparty:
				save(doc, archive_write=False)
		except frappe.ValidationError:
			frappe.db.rollback(save_point=SAVEPOINT)
		except Exception:
			frappe.db.rollback(save_point=SAVEPOINT)
			frappe.log_error(title=f"docarchive: assign {name}")


def missing_copies(target_names) -> list[str]:
	archived = frappe.get_all(
		DOCUMENT, filters={"archive_status": ARCHIVED}, pluck="name", order_by="creation"
	)
	if not archived or not target_names:
		return []
	copies = frappe.get_all(
		"Archive Copy",
		filters={"parenttype": DOCUMENT, "target_name": ("in", list(target_names))},
		fields=["parent", "target_name", "status"],
	)
	have, deleting = {}, set()
	for row in copies:
		have.setdefault(row.parent, set()).add(row.target_name)
		if row.status == DELETED:
			deleting.add(row.parent)
	return [
		name for name in archived if name not in deleting and not set(target_names) <= have.get(name, set())
	]


def eligible(target_names=()) -> list[str]:
	started = frappe.get_all(
		DOCUMENT,
		filters={"archive_status": ("not in", (ARCHIVED, DELETED)), "archive_started_at": ("is", "set")},
		pluck="name",
		order_by="creation",
	) + missing_copies(target_names)
	types = archive_types()
	if not types:
		return started
	cutoff = add_to_date(now_datetime(), hours=-(settings.get().min_age_hours or 0))
	fresh = frappe.get_all(
		DOCUMENT,
		filters={
			"document_type": ("in", types),
			"archive_status": ("!=", ARCHIVED),
			"archive_started_at": ("is", "not set"),
			"modified": ("<=", cutoff),
		},
		fields=CANDIDATE_FIELDS,
		order_by="creation",
	)
	return started + [doc.name for doc in fresh if not missing_fields(doc)]


def planned_key(name):
	doc = frappe.get_doc(DOCUMENT, name)
	return doc.object_key or object_key(doc, content.content_for(doc)[1])


def object_key(doc, ext):
	type_slug = frappe.db.get_value("Business Document Type", doc.document_type, "slug")
	return keys.object_key(doc, type_slug, ext)


def document_lock(name):
	return frappe.cache.lock(frappe.cache.make_key(f"docarchive:document:{name}"), timeout=LOCK_SECONDS)


def archive_document(name, targets, outcomes=None) -> str:
	"""Archive one document. Fills outcomes[name] with (outcome, reason code) for the audit log."""
	outcomes = {} if outcomes is None else outcomes
	lock = document_lock(name)
	if not lock.acquire(blocking=False):
		outcomes[name] = ("skipped", "busy")
		return SKIPPED
	try:
		status = archive_locked(name, targets, outcomes)
	finally:
		release(lock)
	return status


def release(lock):
	try:
		lock.release()
	except Exception:
		pass


def archive_locked(name, targets, outcomes) -> str:
	doc = frappe.get_doc(DOCUMENT, name)
	held_before = doc.legal_hold
	frappe.db.savepoint(SAVEPOINT)
	try:
		data, ext = file_bytes(doc)
		attachments = attachment_bytes(doc)
		if not doc.archive_started_at:
			start(doc, data, ext, targets, attachments)
		add_copies(doc, targets)
		for target in targets:
			copy_to(doc, target, data, attachments)
	except Exception as e:
		frappe.db.rollback(save_point=SAVEPOINT)
		doc = frappe.get_doc(DOCUMENT, name)
		doc.flags.run_error = safe_error(e)
		doc.flags.audit_reason = reason_code(e)
		log(doc, None, doc.flags.run_error)
	doc.archive_queued_at = None
	status = finish(doc, targets)
	if status == ARCHIVED:
		outcomes[name] = ("archived_with_hold" if doc.legal_hold and not held_before else "archived", None)
	else:
		outcomes[name] = ("failed", doc.flags.audit_reason or "save")
	return status


def file_bytes(doc):
	data, ext = content.content_for(doc)
	if data is None:
		raise ArchiveError(_("The file of document {0} cannot be read.").format(doc.name), "unreadable")
	sha256 = content.sha256(data)
	if sha256 != doc.content_sha256 or (doc.archive_sha256 and sha256 != doc.archive_sha256):
		raise ArchiveError(
			_("The file of document {0} no longer matches its checksum.").format(doc.name), "checksum"
		)
	return data, ext


def attachment_bytes(doc) -> list[bytes]:
	result = []
	for row in doc.get("attachments") or []:
		data = content.read_bytes(row.file)
		if data is None:
			raise ArchiveError(
				_("Attachment {0} of document {1} cannot be read.").format(row.idx, doc.name), "unreadable"
			)
		if content.sha256(data) != row.content_sha256:
			raise ArchiveError(
				_("Attachment {0} of document {1} no longer matches its checksum.").format(row.idx, doc.name),
				"checksum",
			)
		result.append(data)
	return result


def start(doc, data, ext, targets, attachments=()):
	years = retention.years_for(doc)
	until = retention.retain_until(doc, years)
	for target in targets:
		store.validate_lock(target.lock_mode, until)
	doc.archive_started_at = now_datetime()
	doc.retain_until = retention.to_db(until)
	doc.object_key = object_key(doc, ext)
	doc.meta_key = keys.meta_key(doc.object_key)
	for number, (row, body) in enumerate(zip(doc.get("attachments") or [], attachments, strict=True), 1):
		row.object_key = keys.attachment_key(
			doc.object_key, number, row.title or row.role, extension(row.file)
		)
		row.archive_size = len(body)
	doc.archive_sha256 = content.sha256(data)
	doc.archive_size = len(data)
	doc.archive_meta = json.dumps(meta(doc, years, until), sort_keys=True)
	if doc.hold_on_archive:
		doc.update({"legal_hold": 1, "legal_hold_reason": _("Set when archiving.")})
		doc.append(
			"legal_hold_log",
			{
				"action": "Set",
				"at": now_datetime(),
				"user": frappe.session.user,
				"reason": _("Set when archiving."),
			},
		)
	save(doc)


def add_copies(doc, targets):
	known = {copy.target_name for copy in doc.archive_copies}
	for target in targets:
		if target.name not in known:
			doc.append(
				"archive_copies",
				{
					"target_name": target.name,
					"status": PENDING,
					"lock_mode": target.lock_mode,
					"retain_until": doc.retain_until,
				},
			)
	save(doc)


def copy_for(doc, target):
	return next(copy for copy in doc.archive_copies if copy.target_name == target.name)


def mime_type(key):
	return mimetypes.guess_type(key)[0] or "application/octet-stream"


def attachment_copy(doc, target_name, key):
	for row in doc.get("attachment_copies") or []:
		if (row.target_name, row.object_key) == (target_name, key):
			return row
	return doc.append("attachment_copies", {"target_name": target_name, "object_key": key})


def copy_to(doc, target, data, attachments=()):
	copy = copy_for(doc, target)
	if copy.status == ARCHIVED:
		return
	try:
		until = retention.from_db(copy.retain_until)
		meta_body = keys.meta_bytes(doc.archive_meta, copy.lock_mode)
		copy.meta_sha256 = copy.meta_sha256 or content.sha256(meta_body)
		hold = bool(doc.legal_hold)
		ensure(
			target,
			doc.object_key,
			data,
			until,
			mime_type(doc.object_key),
			copy,
			"object_version_id",
			legal_hold=hold,
		)
		for row, body in zip(doc.get("attachments") or [], attachments, strict=True):
			record = attachment_copy(doc, target.name, row.object_key)
			ensure(
				target,
				row.object_key,
				body,
				until,
				mime_type(row.object_key),
				copy,
				"version_id",
				record,
				hold,
			)
		ensure(
			target,
			doc.meta_key,
			meta_body,
			until,
			"application/json",
			copy,
			"meta_version_id",
			legal_hold=hold,
		)
		copy.update({"status": ARCHIVED, "archived_at": now_datetime(), "error": None})
		if hold:
			copy.legal_hold = "On"
	except Exception as e:
		copy.update({"status": FAILED, "error": safe_error(e)})
		doc.flags.audit_reason = doc.flags.audit_reason or reason_code(e)
		log(doc, target, copy.error)


def copy_objects(copy, doc) -> list[tuple[str, str]]:
	sidecars = json.loads(copy.sidecar_versions or "{}")
	return [(key, version) for key, version, _sha in locked_objects(doc, copy)] + sorted(sidecars.items())


def missing_versions(copy, doc) -> list[str]:
	recorded = json.loads(copy.sidecar_versions or "{}")
	expected = (
		f"{doc.object_key}.meta.{index}.json" for index in range(2, (doc.retention_extensions or 0) + 2)
	)
	unrecorded = [key for key, version in copy_objects(copy, doc) if not version]
	return unrecorded + [key for key in expected if key not in recorded]


def hold_copy(doc, copy, target) -> str | None:
	try:
		for key, version in copy_objects(copy, doc):
			store.set_hold(target, key, version)
	except Exception as e:
		copy.legal_hold = FAILED
		log(doc, target, safe_error(e))
		return safe_error(e)
	copy.legal_hold = "On"
	return None


def ensure(target, key, body, until, content_type, copy, version_field, record=None, legal_hold=False):
	record = record or copy
	mode, known_version = copy.lock_mode, record.get(version_field)
	sha256 = content.sha256(body)
	store.validate_lock(mode, until)
	existing = store.head(target, key, known_version)
	if existing is None and known_version:
		raise ArchiveError(
			_("The recorded version of {0} is missing on the storage target.").format(key), "missing_version"
		)
	if existing is None:
		version_id = store.put_locked(target, key, body, mode, until, content_type, legal_hold)["version_id"]
	elif (existing.get("Metadata") or {}).get("sha256") != sha256:
		raise ArchiveError(_("{0} already exists with different content.").format(key), "conflict")
	else:
		version_id = known_version or existing.get("VersionId")
	record.set(version_field, version_id)
	store.verify(target, key, version_id, sha256, mode, until, legal_hold)


def finish(doc, targets) -> str:
	enabled = {target.name for target in targets}
	copies = [copy for copy in doc.archive_copies if copy.target_name in enabled]
	if copies and len(copies) == len(enabled) and all(copy.status == ARCHIVED for copy in copies):
		doc.update(
			{
				"archive_status": ARCHIVED,
				"archived_at": doc.archived_at or now_datetime(),
				"retention_mode": mode_summary(doc),
				"archive_error": None,
			}
		)
		return ARCHIVED if store_result(doc) else FAILED
	errors = [f"{copy.target_name}: {copy.error}" for copy in copies if copy.status == FAILED]
	doc.archive_status = FAILED
	doc.archive_attempts = (doc.archive_attempts or 0) + 1
	doc.archive_error = "\n".join(filter(None, [doc.flags.run_error, *errors])) or None
	store_result(doc)
	return FAILED


def store_result(doc) -> bool:
	if doc.archive_started_at:
		try:
			save(doc)
			return True
		except Exception as e:
			frappe.db.rollback(save_point=SAVEPOINT)
			doc.archive_status, doc.archive_error = FAILED, safe_error(e)
	frappe.db.set_value(
		DOCUMENT,
		doc.name,
		{
			"archive_status": doc.archive_status,
			"archive_attempts": doc.archive_attempts,
			"archive_error": doc.archive_error,
			"archive_queued_at": None,
		},
		update_modified=False,
	)
	frappe.db.commit()
	return False


def mode_summary(doc) -> str:
	return ", ".join(sorted({copy.lock_mode for copy in doc.archive_copies}))


def save(doc, archive_write=True):
	frappe.db.savepoint(SAVEPOINT)
	doc.flags.archive_write = archive_write
	doc.save(ignore_permissions=True)
	frappe.db.commit()


def safe_error(error) -> str:
	code = store.error_code(error)
	if code:
		return _("Storage error {0}").format(code)
	if isinstance(error, frappe.ValidationError):
		return str(error)
	return type(error).__name__


def log(doc, target, message):
	where = f" ({target.name})" if target else ""
	frappe.log_error(title=f"docarchive: {doc.name}{where}", message=message)


def utc_iso(value):
	local = value.replace(tzinfo=ZoneInfo(get_system_timezone())) if value.tzinfo is None else value
	return local.astimezone(UTC).isoformat().replace("+00:00", "Z")


def counterparty(doc):
	if not doc.counterparty:
		return None
	party = frappe.get_doc(doc.counterparty_type, doc.counterparty)
	return {"doctype": doc.counterparty_type, "id": doc.counterparty, "name": party.get_title() or party.name}


def correspondence_meta(doc):
	if not frappe.get_cached_value("Business Document Type", doc.document_type, "is_correspondence"):
		return None
	return {
		"channel": doc.correspondence_channel,
		"direction": doc.correspondence_direction,
		"at": utc_iso(frappe.utils.get_datetime(doc.correspondence_at)) if doc.correspondence_at else None,
		"thread_key": doc.thread_key,
		"mail": archiving.mail_meta(doc),
	}


def meta(doc, years, until) -> dict:
	file = own_file(doc) if doc.file else None
	document_type = frappe.get_cached_doc("Business Document Type", doc.document_type)
	main = {
		"key": doc.object_key,
		"name": file.file_name if file else (doc.file or "").rsplit("/", 1)[-1] or None,
		"role": MAIN_ROLE,
		"sha256": doc.archive_sha256,
		"size": doc.archive_size,
	}
	attachments = [
		{
			"key": row.object_key,
			"name": file_name(doc, row.file),
			"role": row.role,
			"title": row.title,
			"sha256": row.content_sha256,
			"size": row.archive_size,
		}
		for row in doc.get("attachments") or []
	]
	return {
		"format": "docarchive-meta/1",
		"archived_at": utc_iso(frappe.utils.get_datetime(doc.archive_started_at)),
		"document": {
			"id": doc.name,
			"title": doc.title,
			"type": document_type.name,
			"type_slug": document_type.slug,
			"date": str(doc.document_date),
			"number": doc.document_number,
			"contract_end_date": str(doc.contract_end_date) if doc.contract_end_date else None,
			"own_company": doc.own_company,
		},
		"counterparty": counterparty(doc),
		"correspondence": correspondence_meta(doc),
		"einvoice": json.loads(doc.einvoice_data) if doc.get("einvoice_data") else None,
		"file": main,
		"files": [main, *attachments],
		"source": {
			"system": doc.source_system,
			"account": doc.source_account,
			"party_type": doc.source_party_type,
			"party_id": doc.source_party_id,
			"party_name": doc.source_party_name,
			"party_email": doc.source_party_email,
			"key": doc.source_key,
			"doctype": doc.source_doctype,
			"name": doc.source_name,
			"replaces": doc.replaces,
		},
		"retention": {
			"counts_from": document_type.retention_from,
			"start_date": str(retention.start_date(doc)),
			"years": years,
			"retain_until": utc_iso(until),
		},
	}


def extensions(target_names):
	types = frappe.get_all(
		"Business Document Type", filters={"retention_from": retention.CONTRACT_END}, pluck="name"
	)
	pending = frappe.get_all(
		DOCUMENT,
		filters={
			"extension_until": ("is", "set"),
			"archived_at": ("is", "set"),
			"archive_status": ("!=", DELETED),
		},
		pluck="name",
	)
	names = frappe.get_all(
		DOCUMENT,
		filters={
			"document_type": ("in", types or [""]),
			"archived_at": ("is", "set"),
			"archive_status": ("!=", DELETED),
			"contract_end_date": ("is", "set"),
		},
		pluck="name",
		order_by="creation",
	)
	names = list(dict.fromkeys([*pending, *names]))
	result = []
	for name in names:
		try:
			doc = frappe.get_doc(DOCUMENT, name)
			if any(copy.status == DELETED for copy in doc.archive_copies):
				continue
			until = retention.from_db(doc.extension_until) or contract_until(doc)
			if not until:
				continue
			copies = archived_copies(doc, target_names)
			if until > retention.from_db(doc.retain_until) or any(
				retention.from_db(copy.retain_until) < until for copy in copies
			):
				result.append((doc, until))
		except Exception as e:
			frappe.log_error(title=f"docarchive: {name}", message=safe_error(e))
	return result


def contract_until(doc):
	if doc.contract_end_date and retention.counts_from_contract_end(doc):
		return retention.retain_until(doc, frozen_years(doc))
	return None


def archived_copies(doc, target_names):
	return [c for c in doc.archive_copies if c.target_name in target_names and c.status == ARCHIVED]


def frozen_years(doc) -> int:
	return json.loads(doc.archive_meta)["retention"]["years"]


def sidecar_key(doc) -> str:
	return f"{doc.object_key}.meta.{(doc.retention_extensions or 0) + 2}.json"


def prepare_extension(doc, until, note=None):
	if doc.extension_until:
		return
	history = json.loads(doc.retention_history or "[]")
	entry = {
		"index": (doc.retention_extensions or 0) + 2,
		"previous_retain_until": utc_iso(retention.from_db(doc.retain_until)),
		"retain_until": utc_iso(until),
		"contract_end_date": str(doc.contract_end_date) if doc.contract_end_date else None,
		"extended_at": utc_iso(frappe.utils.get_datetime(now_datetime())),
		**(note or {}),
	}
	sidecar = json.loads(doc.archive_meta)
	sidecar["retention"] = {
		**sidecar["retention"],
		"start_date": str(retention.start_date(doc)),
		"retain_until": utc_iso(until),
	}
	sidecar["document"]["contract_end_date"] = str(doc.contract_end_date) if doc.contract_end_date else None
	sidecar["extension"] = entry
	sidecar["history"] = history
	doc.extension_until = retention.to_db(until)
	doc.extension_meta = json.dumps(sidecar, sort_keys=True)
	save(doc)


def locked_objects(doc, copy) -> list[tuple[str, str, str]]:
	versions = {
		row.object_key: row.version_id
		for row in doc.get("attachment_copies") or []
		if row.target_name == copy.target_name
	}
	return [
		(doc.object_key, copy.object_version_id, doc.archive_sha256),
		*(
			(row.object_key, versions.get(row.object_key), row.content_sha256)
			for row in doc.get("attachments") or []
		),
		(doc.meta_key, copy.meta_version_id, copy.meta_sha256),
	]


def extend_copy(doc, copy, target, until):
	copy.sidecar_version_id = None
	for key, version, sha256 in locked_objects(doc, copy):
		store.extend_locked(target, key, version, sha256, copy.lock_mode, until)
	body = keys.meta_bytes(doc.extension_meta, copy.lock_mode)
	hold = bool(doc.legal_hold)
	ensure(
		target, sidecar_key(doc), body, until, "application/json", copy, "sidecar_version_id", legal_hold=hold
	)
	sidecars = json.loads(copy.sidecar_versions or "{}")
	sidecars[sidecar_key(doc)] = copy.sidecar_version_id
	copy.sidecar_versions = json.dumps(sidecars, sort_keys=True)
	copy.update({"retain_until": retention.to_db(until), "error": None})


def extend_retention(targets, documents=None) -> dict:
	by_name = {target.name: target for target in targets}
	results = {ARCHIVED: [], FAILED: []}
	documents = audit.Batch() if documents is None else documents
	for doc, until in extensions(set(by_name)):
		lock = document_lock(doc.name)
		if not lock.acquire(blocking=False):
			continue
		try:
			documents.start(DOCUMENT, doc.name)
			done = extend_document(doc, until, by_name)
			results[ARCHIVED if done else FAILED].append(doc.name)
			documents.done(DOCUMENT, doc.name, "extended" if done else "extension_failed")
		finally:
			release(lock)
	return results


def extend_document(doc, until, by_name) -> bool:
	frappe.db.savepoint(SAVEPOINT)
	try:
		prepare_extension(doc, until)
	except Exception as e:
		frappe.db.rollback(save_point=SAVEPOINT)
		log(doc, None, safe_error(e))
		return False
	copies = archived_copies(doc, by_name)
	for copy in copies:
		if retention.from_db(copy.retain_until) >= until:
			continue
		try:
			extend_copy(doc, copy, by_name[copy.target_name], until)
		except Exception as e:
			copy.error = safe_error(e)
			log(doc, by_name[copy.target_name], copy.error)
	done = all(retention.from_db(copy.retain_until) >= until for copy in copies)
	if done:
		history = json.loads(doc.retention_history or "[]")
		history.append(json.loads(doc.extension_meta)["extension"])
		kept = max(until, retention.from_db(doc.retain_until) or until)
		doc.update(
			{
				"retain_until": retention.to_db(kept),
				"retention_extensions": (doc.retention_extensions or 0) + 1,
				"retention_history": json.dumps(history, sort_keys=True),
				"extension_until": None,
				"extension_meta": None,
			}
		)
	try:
		save(doc)
	except Exception as e:
		frappe.db.rollback(save_point=SAVEPOINT)
		log(doc, None, safe_error(e))
		return False
	return done


def sync_mode(target_name) -> dict:
	target = smoke_target(target_name)
	result = {"target": target.name, "checked": 0, "updated": [], "mismatched": [], "errors": []}
	for name in frappe.get_all(
		"Archive Copy",
		filters={"parenttype": DOCUMENT, "target_name": target.name, "status": ARCHIVED},
		pluck="parent",
		order_by="creation",
	):
		result["checked"] += 1
		try:
			status = sync_document(name, target)
		except Exception as e:
			frappe.db.rollback(save_point=SAVEPOINT)
			result["errors"].append(f"{name}: {safe_error(e)}")
			continue
		if status:
			result[status].append(name)
	return result


def sync_document(name, target):
	frappe.db.savepoint(SAVEPOINT)
	doc = frappe.get_doc(DOCUMENT, name)
	copy = next(c for c in doc.archive_copies if c.target_name == target.name)
	modes = {
		store.retention(target, key, version)["mode"] for key, version, _sha in locked_objects(doc, copy)
	}
	if modes == {copy.lock_mode}:
		return None
	if modes == {"COMPLIANCE"} and copy.lock_mode == "GOVERNANCE":
		copy.lock_mode = "COMPLIANCE"
		doc.retention_mode = mode_summary(doc)
		save(doc)
		return "updated"
	return "mismatched"


def smoke(target_name) -> dict:
	target = smoke_target(target_name)
	until = datetime.now(UTC).replace(microsecond=0) + timedelta(days=1)
	key = f"{store.SMOKE_PREFIX}{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}.txt"
	body = f"docarchive smoke test {until.isoformat()}\n".encode()
	put = store.put_locked(target, key, body, "GOVERNANCE", until, "text/plain")
	version = put["version_id"]
	checks = {"put": True, "versioned": bool(version)}
	if version:
		checks["sha256"] = store.read_sha256(target, key, version) == put["sha256"]
		lock = store.retention(target, key, version)
		checks["retention"] = lock["mode"] == "GOVERNANCE" and same_second(lock["retain_until"], until)
		checks["delete_refused"] = delete_refused(target, key, version)
		checks["bypass_delete_refused"] = delete_refused(target, key, version, bypass=True)
		checks["still_there"] = store.head(target, key, version) is not None
	return {"target": target.name, "key": key, "checks": checks, "ok": all(checks.values())}


def delete_refused(target, key, version, bypass=False) -> bool:
	try:
		store.smoke_delete(target, key, version, bypass=bypass)
	except Exception as e:
		return store.refused(e)
	return False


def same_second(value, expected) -> bool:
	if isinstance(value, str):
		value = datetime.fromisoformat(value.replace("Z", "+00:00"))
	return bool(value) and abs((value - expected).total_seconds()) < 1


def smoke_target(name) -> store.Target:
	for row in frappe.get_single("Archive Settings").storage_targets:
		if row.target_name == name:
			config = storage_targets.target(row)
			missing = [field for field in store.CLIENT_FIELDS if not config.get(field)]
			if missing:
				frappe.throw(_("Storage target {0} is missing: {1}").format(name, ", ".join(missing)))
			return store.from_config(config)
	frappe.throw(_("There is no storage target named {0}.").format(name))
