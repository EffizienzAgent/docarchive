import json
import mimetypes
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import frappe
from frappe import _
from frappe.utils import add_to_date, get_system_timezone, now_datetime

from docarchive import assignment, content, keys, retention, settings, storage_targets, store
from docarchive.document_archive.doctype.business_document.business_document import (
	extension,
	missing_fields,
	own_file,
)

DOCUMENT = "Business Document"
SAVEPOINT = "docarchive_save"
PENDING, ARCHIVED, FAILED = "Pending", "Archived", "Failed"
CANDIDATE_FIELDS = [
	"name",
	"document_type",
	"file",
	"counterparty",
	"document_number",
	"contract_end_date",
	"archive_started_at",
]


class ArchiveError(frappe.ValidationError):
	pass


def schedule():
	frappe.enqueue("docarchive.run.nightly", queue="long", job_id="docarchive-nightly", deduplicate=True)


def nightly(dry_run=False) -> dict:
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
			"documents": [{"name": name, "object_key": planned_key(name)} for name in eligible()],
		}
	reassign()
	results = {ARCHIVED: [], FAILED: []}
	for name in eligible():
		results[archive_document(name, targets)].append(name)
	summary = {"status": "done", "archived": len(results[ARCHIVED]), "failed": results[FAILED]}
	frappe.logger("docarchive").info(summary)
	return summary


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
			"source_system": ("is", "set"),
		},
		pluck="name",
	):
		doc = frappe.get_doc(DOCUMENT, name)
		if assignment.assign(doc) and doc.counterparty:
			try:
				save(doc, archive_write=False)
			except frappe.ValidationError:
				frappe.db.rollback(save_point=SAVEPOINT)


def eligible() -> list[str]:
	started = frappe.get_all(
		DOCUMENT,
		filters={"archive_status": ("!=", ARCHIVED), "archive_started_at": ("is", "set")},
		pluck="name",
		order_by="creation",
	)
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
	return doc.object_key or object_key(doc)


def object_key(doc):
	type_slug = frappe.db.get_value("Business Document Type", doc.document_type, "slug")
	return keys.object_key(doc, type_slug, extension(doc.file))


def archive_document(name, targets) -> str:
	doc = frappe.get_doc(DOCUMENT, name)
	frappe.db.savepoint(SAVEPOINT)
	try:
		data = file_bytes(doc)
		if not doc.archive_started_at:
			start(doc, data, targets)
		add_copies(doc, targets)
		for target in targets:
			copy_to(doc, target, data)
	except Exception as e:
		frappe.db.rollback(save_point=SAVEPOINT)
		doc = frappe.get_doc(DOCUMENT, name)
		doc.archive_error = safe_error(e)
		log(doc, None, doc.archive_error)
	return finish(doc, targets)


def file_bytes(doc):
	data = content.read_bytes(doc.file)
	if data is None:
		raise ArchiveError(_("The file of document {0} cannot be read.").format(doc.name))
	sha256 = content.sha256(data)
	if sha256 != doc.content_sha256 or (doc.archive_sha256 and sha256 != doc.archive_sha256):
		raise ArchiveError(_("The file of document {0} no longer matches its checksum.").format(doc.name))
	return data


def start(doc, data, targets):
	years = retention.years_for(doc)
	until = retention.retain_until(doc, years)
	for target in targets:
		store.validate_lock(target.lock_mode, until)
	doc.archive_started_at = now_datetime()
	doc.retain_until = retention.to_db(until)
	doc.object_key = object_key(doc)
	doc.meta_key = keys.meta_key(doc.object_key)
	doc.archive_sha256 = content.sha256(data)
	doc.archive_size = len(data)
	doc.archive_meta = json.dumps(meta(doc, years, until), sort_keys=True)
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


def copy_to(doc, target, data):
	copy = copy_for(doc, target)
	if copy.status == ARCHIVED:
		return
	try:
		until = retention.from_db(copy.retain_until)
		mime = mimetypes.guess_type(doc.object_key)[0] or "application/octet-stream"
		objects = (
			(doc.object_key, data, mime),
			(doc.meta_key, keys.meta_bytes(doc.archive_meta, copy.lock_mode), "application/json"),
		)
		for key, body, content_type in objects:
			ensure(target, key, body, copy.lock_mode, until, content_type)
		if store.read_sha256(target, doc.object_key) != doc.archive_sha256:
			raise ArchiveError(_("The archived copy does not match the checksum."))
		copy.update({"status": ARCHIVED, "archived_at": now_datetime(), "error": None})
	except Exception as e:
		copy.update({"status": FAILED, "error": safe_error(e)})
		log(doc, target, copy.error)


def ensure(target, key, body, mode, until, content_type):
	existing = store.head(target, key)
	if existing is None:
		store.put_locked(target, key, body, mode, until, content_type)
		return
	if (existing.get("Metadata") or {}).get("sha256") != content.sha256(body):
		raise ArchiveError(_("{0} already exists with different content.").format(key))


def finish(doc, targets) -> str:
	enabled = {target.name for target in targets}
	copies = [copy for copy in doc.archive_copies if copy.target_name in enabled]
	if copies and len(copies) == len(enabled) and all(copy.status == ARCHIVED for copy in copies):
		doc.update(
			{
				"archive_status": ARCHIVED,
				"archived_at": now_datetime(),
				"retention_mode": mode_summary(doc),
				"archive_error": None,
			}
		)
		return ARCHIVED if store_result(doc) else FAILED
	errors = [f"{copy.target_name}: {copy.error}" for copy in copies if copy.status == FAILED]
	doc.archive_status = FAILED
	doc.archive_attempts = (doc.archive_attempts or 0) + 1
	doc.archive_error = "\n".join(filter(None, [doc.archive_error, *errors])) or None
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


def meta(doc, years, until) -> dict:
	file = own_file(doc)
	document_type = frappe.get_cached_doc("Business Document Type", doc.document_type)
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
		"file": {
			"key": doc.object_key,
			"name": file.file_name if file else doc.file.rsplit("/", 1)[-1],
			"sha256": doc.archive_sha256,
			"size": doc.archive_size,
		},
		"source": {
			"system": doc.source_system,
			"account": doc.source_account,
			"party_type": doc.source_party_type,
			"party_id": doc.source_party_id,
			"party_name": doc.source_party_name,
			"party_email": doc.source_party_email,
			"key": doc.source_key,
			"replaces": doc.replaces,
		},
		"retention": {
			"counts_from": document_type.retention_from,
			"start_date": str(retention.start_date(doc)),
			"years": years,
			"retain_until": utc_iso(until),
		},
	}


def smoke(target_name) -> dict:
	target = smoke_target(target_name)
	until = datetime.now(UTC).replace(microsecond=0) + timedelta(days=1)
	key = f"{store.SMOKE_PREFIX}{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}.txt"
	body = f"docarchive smoke test {until.isoformat()}\n".encode()
	checks = {}
	put = store.put_locked(target, key, body, "GOVERNANCE", until, "text/plain")
	checks["put"] = True
	checks["sha256"] = store.read_sha256(target, key) == put["sha256"]
	lock = store.retention(target, key, put["version_id"])
	checks["retention"] = lock["mode"] == "GOVERNANCE" and same_second(lock["retain_until"], until)
	checks["versioned"] = bool(put["version_id"])
	try:
		store.smoke_delete(target, key, put["version_id"])
		checks["delete_refused"] = False
	except Exception as e:
		checks["delete_refused"] = store.error_code(e) is not None
	return {"target": target.name, "key": key, "checks": checks, "ok": all(checks.values())}


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
