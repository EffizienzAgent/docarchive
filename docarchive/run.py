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
	missing_fields,
	own_file,
)

DOCUMENT = "Business Document"
SAVEPOINT = "docarchive_save"
LOCK_SECONDS = 1800
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
	results = {ARCHIVED: [], FAILED: []}
	for name in eligible({t.name for t in targets}):
		results[archive_document(name, targets)].append(name)
	extended = extend_retention(targets)
	summary = {
		"status": "done",
		"archived": len(results[ARCHIVED]),
		"failed": results[FAILED],
		"extended": len(extended[ARCHIVED]),
		"extension_failed": extended[FAILED],
	}
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


def missing_copies(target_names) -> list[str]:
	archived = frappe.get_all(
		DOCUMENT, filters={"archive_status": ARCHIVED}, pluck="name", order_by="creation"
	)
	if not archived or not target_names:
		return []
	copies = frappe.get_all(
		"Archive Copy",
		filters={"parenttype": DOCUMENT, "target_name": ("in", list(target_names))},
		fields=["parent", "target_name"],
	)
	have = {}
	for row in copies:
		have.setdefault(row.parent, set()).add(row.target_name)
	return [name for name in archived if not set(target_names) <= have.get(name, set())]


def eligible(target_names=()) -> list[str]:
	started = frappe.get_all(
		DOCUMENT,
		filters={"archive_status": ("!=", ARCHIVED), "archive_started_at": ("is", "set")},
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


def archive_document(name, targets) -> str:
	lock = document_lock(name)
	if not lock.acquire(blocking=False):
		return frappe.db.get_value(DOCUMENT, name, "archive_status")
	try:
		return archive_locked(name, targets)
	finally:
		release(lock)


def release(lock):
	try:
		lock.release()
	except Exception:
		pass


def archive_locked(name, targets) -> str:
	doc = frappe.get_doc(DOCUMENT, name)
	frappe.db.savepoint(SAVEPOINT)
	try:
		data, ext = file_bytes(doc)
		if not doc.archive_started_at:
			start(doc, data, ext, targets)
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
	data, ext = content.content_for(doc)
	if data is None:
		raise ArchiveError(_("The file of document {0} cannot be read.").format(doc.name))
	sha256 = content.sha256(data)
	if sha256 != doc.content_sha256 or (doc.archive_sha256 and sha256 != doc.archive_sha256):
		raise ArchiveError(_("The file of document {0} no longer matches its checksum.").format(doc.name))
	return data, ext


def start(doc, data, ext, targets):
	years = retention.years_for(doc)
	until = retention.retain_until(doc, years)
	for target in targets:
		store.validate_lock(target.lock_mode, until)
	doc.archive_started_at = now_datetime()
	doc.retain_until = retention.to_db(until)
	doc.object_key = object_key(doc, ext)
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
		meta_body = keys.meta_bytes(doc.archive_meta, copy.lock_mode)
		copy.meta_sha256 = copy.meta_sha256 or content.sha256(meta_body)
		ensure(target, doc.object_key, data, until, mime, copy, "object_version_id")
		ensure(target, doc.meta_key, meta_body, until, "application/json", copy, "meta_version_id")
		copy.update({"status": ARCHIVED, "archived_at": now_datetime(), "error": None})
	except Exception as e:
		copy.update({"status": FAILED, "error": safe_error(e)})
		log(doc, target, copy.error)


def ensure(target, key, body, until, content_type, copy, version_field):
	mode, known_version = copy.lock_mode, copy.get(version_field)
	sha256 = content.sha256(body)
	store.validate_lock(mode, until)
	existing = store.head(target, key)
	if existing is None:
		version_id = store.put_locked(target, key, body, mode, until, content_type)["version_id"]
	elif (existing.get("Metadata") or {}).get("sha256") != sha256:
		raise ArchiveError(_("{0} already exists with different content.").format(key))
	else:
		version_id = existing.get("VersionId")
	if known_version and version_id != known_version:
		raise ArchiveError(_("{0} has a new version on the storage target.").format(key))
	copy.set(version_field, version_id)
	store.verify(target, key, version_id, sha256, mode, until)


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
	file = own_file(doc) if doc.file else None
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
			"name": file.file_name if file else (doc.file or "").rsplit("/", 1)[-1] or None,
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
	if not types:
		return []
	names = frappe.get_all(
		DOCUMENT,
		filters={
			"document_type": ("in", types),
			"archive_status": ARCHIVED,
			"contract_end_date": ("is", "set"),
		},
		pluck="name",
		order_by="creation",
	)
	result = []
	for name in names:
		try:
			doc = frappe.get_doc(DOCUMENT, name)
			until = retention.from_db(doc.extension_until) or retention.retain_until(doc, frozen_years(doc))
			copies = [copy for copy in doc.archive_copies if copy.target_name in target_names]
			if until > retention.from_db(doc.retain_until) or any(
				retention.from_db(copy.retain_until) < until for copy in copies
			):
				result.append((doc, until))
		except Exception as e:
			frappe.log_error(title=f"docarchive: {name}", message=safe_error(e))
	return result


def frozen_years(doc) -> int:
	return json.loads(doc.archive_meta)["retention"]["years"]


def sidecar_key(doc) -> str:
	return f"{doc.object_key}.meta.{(doc.retention_extensions or 0) + 2}.json"


def prepare_extension(doc, until):
	if doc.extension_until:
		return
	history = json.loads(doc.retention_history or "[]")
	entry = {
		"index": (doc.retention_extensions or 0) + 2,
		"previous_retain_until": utc_iso(retention.from_db(doc.retain_until)),
		"retain_until": utc_iso(until),
		"contract_end_date": str(doc.contract_end_date),
		"extended_at": utc_iso(frappe.utils.get_datetime(now_datetime())),
	}
	sidecar = json.loads(doc.archive_meta)
	sidecar["retention"] = {
		**sidecar["retention"],
		"start_date": str(retention.start_date(doc)),
		"retain_until": utc_iso(until),
	}
	sidecar["document"]["contract_end_date"] = str(doc.contract_end_date)
	sidecar["extension"] = entry
	sidecar["history"] = history
	doc.extension_until = retention.to_db(until)
	doc.extension_meta = json.dumps(sidecar, sort_keys=True)
	save(doc)


def extend_copy(doc, copy, target, until):
	copy.sidecar_version_id = None
	objects = (
		(doc.object_key, copy.object_version_id, doc.archive_sha256),
		(doc.meta_key, copy.meta_version_id, copy.meta_sha256),
	)
	for key, version, sha256 in objects:
		store.extend_locked(target, key, version, sha256, copy.lock_mode, until)
	body = keys.meta_bytes(doc.extension_meta, copy.lock_mode)
	ensure(target, sidecar_key(doc), body, until, "application/json", copy, "sidecar_version_id")
	copy.update({"retain_until": retention.to_db(until), "error": None})


def extend_retention(targets) -> dict:
	by_name = {target.name: target for target in targets}
	results = {ARCHIVED: [], FAILED: []}
	for doc, until in extensions(set(by_name)):
		lock = document_lock(doc.name)
		if not lock.acquire(blocking=False):
			continue
		try:
			results[ARCHIVED if extend_document(doc, until, by_name) else FAILED].append(doc.name)
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
	copies = [copy for copy in doc.archive_copies if copy.target_name in by_name]
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
		doc.update(
			{
				"retain_until": retention.to_db(until),
				"retention_extensions": (doc.retention_extensions or 0) + 1,
				"retention_history": json.dumps(history, sort_keys=True),
				"extension_until": None,
				"extension_meta": None,
			}
		)
	try:
		save(doc)
	except Exception:
		frappe.db.rollback(save_point=SAVEPOINT)
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
	objects = ((doc.object_key, copy.object_version_id), (doc.meta_key, copy.meta_version_id))
	modes = {store.retention(target, key, version)["mode"] for key, version in objects}
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
