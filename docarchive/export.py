import json
import os
import shutil
import tempfile
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import frappe
from frappe import _
from frappe.utils import add_days, cint, getdate, now_datetime

from docarchive import export_package, run, store
from docarchive.legal_hold import is_admin, targets_by_name
from docarchive.permissions import ADMIN_ROLE

EXPORT = "Archive Export"
DOCUMENT = run.DOCUMENT
QUEUED, RUNNING, COMPLETED, FAILED, EXPIRED = "Queued", "Running", "Completed", "Failed", "Expired"
EVENT = "docarchive_export"
FILTERS = ("own_company", "document_type", "date_from", "date_to", "counterparty_type", "counterparty")
WORKERS = 4
ATTEMPTS = 3
DEFAULT_PART_MB = 2048
DEFAULT_EXPIRY_DAYS = 7
META_SIZE = 64 * 1024


class ExportError(frappe.ValidationError):
	pass


@frappe.whitelist()
def access() -> dict:
	return {"export": bool(is_admin() and store.targets()), "targets": sorted(targets_by_name())}


def clean_scope(scope) -> dict:
	scope = json.loads(scope) if isinstance(scope, str) else dict(scope or {})
	clean = {field: scope.get(field) or None for field in (*FILTERS, "target_name")}
	for field in ("date_from", "date_to"):
		if clean[field]:
			clean[field] = str(getdate(clean[field]))
	if clean["date_from"] and clean["date_to"] and clean["date_from"] > clean["date_to"]:
		frappe.throw(_("The start date must not be after the end date."))
	if clean["counterparty"] and not clean["counterparty_type"]:
		frappe.throw(_("Please choose the counterparty type."))
	if clean["counterparty_type"] and not frappe.db.exists("DocType", clean["counterparty_type"]):
		frappe.throw(_("Unknown counterparty type {0}.").format(clean["counterparty_type"]))
	by_name = targets_by_name()
	if not by_name:
		frappe.throw(_("No storage target is configured."))
	clean["target_name"] = clean["target_name"] or next(iter(by_name))
	if clean["target_name"] not in by_name:
		frappe.throw(_("Unknown storage target {0}.").format(clean["target_name"]))
	return clean


@frappe.whitelist(methods=["POST"])
def start_export(scope=None) -> str:
	frappe.only_for(ADMIN_ROLE)
	scope = clean_scope(scope)
	doc = frappe.get_doc(
		{
			"doctype": EXPORT,
			"status": QUEUED,
			"requested_by": frappe.session.user,
			"requested_at": now_datetime(),
			**scope,
		}
	).insert(ignore_permissions=True)
	frappe.enqueue(
		"docarchive.export.run_export",
		queue="long",
		timeout=6 * 3600,
		job_id=f"docarchive-export-{doc.name}",
		deduplicate=True,
		enqueue_after_commit=True,
		name=doc.name,
	)
	return doc.name


def selected(export) -> list[str]:
	filters = {"archive_status": run.ARCHIVED}
	for field in ("own_company", "document_type", "counterparty_type", "counterparty"):
		if export.get(field):
			filters[field] = export.get(field)
	if export.date_from and export.date_to:
		filters["document_date"] = ("between", [export.date_from, export.date_to])
	elif export.date_from:
		filters["document_date"] = (">=", export.date_from)
	elif export.date_to:
		filters["document_date"] = ("<=", export.date_to)
	return frappe.get_all(
		DOCUMENT, filters=filters, pluck="name", order_by="document_date asc, name asc", limit_page_length=0
	)


def copy_on(doc, target_name):
	return next(
		(c for c in doc.archive_copies if c.target_name == target_name and c.status == run.ARCHIVED), None
	)


def planned_size(doc) -> int:
	files = (doc.archive_size or 0) + sum(row.archive_size or 0 for row in doc.get("attachments") or [])
	return files + META_SIZE * (2 + (doc.retention_extensions or 0))


def part_limit() -> int:
	value = cint(frappe.db.get_single_value("Archive Settings", "export_part_size_mb")) or DEFAULT_PART_MB
	return value * 1024 * 1024


def plan_parts(entries, limit) -> list[list]:
	parts, current, size = [], [], 0
	for entry in entries:
		if current and size + entry["size"] > limit:
			parts.append(current)
			current, size = [], 0
		current.append(entry)
		size += entry["size"]
	if current:
		parts.append(current)
	return parts


def run_export(name):
	export = frappe.get_doc(EXPORT, name)
	if export.status != QUEUED:
		return export.status
	export.db_set("status", RUNNING)
	frappe.db.commit()
	workdir = None
	try:
		target = targets_by_name().get(export.target_name)
		if not target:
			raise ExportError(_("Unknown storage target {0}.").format(export.target_name))
		entries, skipped = [], 0
		for doc_name in selected(export):
			doc = frappe.get_doc(DOCUMENT, doc_name)
			copy = copy_on(doc, export.target_name)
			if copy is None or run.missing_versions(copy, doc):
				skipped += 1
				continue
			entries.append({"doc": doc_name, "size": planned_size(doc)})
		parts = plan_parts(entries, part_limit())
		export.db_set({"total_documents": len(entries), "skipped_documents": skipped})
		workdir = tempfile.mkdtemp(prefix="docarchive-export-")
		check_space(workdir, max((sum(e["size"] for e in part) for part in parts), default=0))
		progress = Progress(export, len(entries))
		mismatches, total_size = [], 0
		for number, part in enumerate(parts, start=1):
			result = build_part(export, target, part, number, len(parts), workdir, progress)
			mismatches += result["mismatches"]
			total_size += result["size"]
		export.reload()
		export.update(
			{
				"status": COMPLETED,
				"progress": 100,
				"exported_documents": len(entries),
				"archive_size": total_size,
				"mismatches": len(mismatches),
				"mismatch_details": json.dumps(mismatches, indent=1) if mismatches else None,
				"expires_at": add_days(now_datetime(), expiry_days()),
			}
		)
		export.save(ignore_permissions=True)
		frappe.db.commit()
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title=f"docarchive: export {name}")
		delete_files(name)
		frappe.db.set_value(EXPORT, name, {"status": FAILED, "error": run.safe_error(e)})
		frappe.db.commit()
	finally:
		if workdir:
			shutil.rmtree(workdir, ignore_errors=True)
	status = frappe.db.get_value(EXPORT, name, "status")
	frappe.publish_realtime(EVENT, {"name": name, "status": status}, user=export.requested_by)
	return status


def check_space(workdir, largest_part):
	free = shutil.disk_usage(workdir).free
	needed = int(largest_part * 1.1) + 64 * 1024 * 1024
	if free < needed:
		raise ExportError(
			_("Not enough disk space for the export: {0} MB needed, {1} MB free.").format(
				needed // 2**20, free // 2**20
			)
		)


def expiry_days() -> int:
	value = frappe.db.get_single_value("Archive Settings", "export_expiry_days")
	return cint(value) if value not in (None, "") else DEFAULT_EXPIRY_DAYS


class Progress:
	def __init__(self, export, total):
		self.export, self.total, self.done, self.shown = export, total, 0, -1

	def step(self):
		self.done += 1
		percent = int(self.done * 100 / self.total) if self.total else 100
		if percent == self.shown:
			return
		self.shown = percent
		frappe.db.set_value(EXPORT, self.export.name, {"progress": percent, "exported_documents": self.done})
		frappe.db.commit()
		frappe.publish_realtime(
			EVENT,
			{"name": self.export.name, "status": RUNNING, "done": self.done, "total": self.total},
			user=self.export.requested_by,
		)


def fetch(target, key, version_id, workdir) -> dict:
	for attempt in range(1, ATTEMPTS + 1):
		handle, path = tempfile.mkstemp(dir=workdir)
		try:
			with os.fdopen(handle, "wb") as out:
				result = store.download(target, key, version_id, out)
			return {**result, "path": path}
		except Exception as e:
			os.unlink(path)
			if attempt == ATTEMPTS:
				return {"exception": e, "path": None}
	raise AssertionError


def fetched(target, objects, workdir):
	window = deque()
	with ThreadPoolExecutor(max_workers=WORKERS) as pool:
		for item in objects:
			window.append((item, pool.submit(fetch, target, item["key"], item["version_id"], workdir)))
			if len(window) >= WORKERS * 2:
				first, future = window.popleft()
				yield first, future.result()
		while window:
			first, future = window.popleft()
			yield first, future.result()


def objects_of(doc, copy) -> list[dict]:
	recorded = {key: sha256 for key, _version, sha256 in run.locked_objects(doc, copy)}
	return [
		{"doc": doc.name, "key": key, "version_id": version, "sha256": recorded.get(key)}
		for key, version in run.copy_objects(copy, doc)
	]


def build_part(export, target, part, number, count, workdir, progress) -> dict:
	docs = {entry["doc"]: frappe.get_doc(DOCUMENT, entry["doc"]) for entry in part}
	copies = {name: copy_on(doc, export.target_name) for name, doc in docs.items()}
	objects = [item for name, doc in docs.items() for item in objects_of(doc, copies[name])]
	label = f"{export.name}-{number}" if count > 1 else export.name
	path = os.path.join(workdir, f"{label}.zip")
	package = export_package.Package(path, export_package.scope_of(export, number, count))
	remaining = {name: sum(1 for item in objects if item["doc"] == name) for name in docs}
	for item, result in fetched(target, objects, workdir):
		if "exception" in result:
			result["error"] = run.safe_error(result.pop("exception"))
		try:
			package.add_object(docs[item["doc"]], item, result)
		finally:
			if result["path"]:
				os.unlink(result["path"])
		remaining[item["doc"]] -= 1
		if not remaining[item["doc"]]:
			package.finish_document(docs[item["doc"]], copies[item["doc"]])
			progress.step()
	package.close()
	size = os.path.getsize(path)
	file = store_file(export, path, f"{label}.zip")
	os.unlink(path)
	current = frappe.get_doc(EXPORT, export.name)
	current.append(
		"parts", {"part": number, "file": file.file_url, "documents": len(docs), "archive_size": size}
	)
	current.save(ignore_permissions=True)
	frappe.db.commit()
	return {"mismatches": package.mismatches, "size": size}


def store_file(export, path, file_name):
	from s3storage.backend import insert_file

	return insert_file(
		file_name,
		path,
		is_private=1,
		ignore_permissions=True,
		attached_to_doctype=EXPORT,
		attached_to_name=export.name,
	)


def expire():
	names = frappe.get_all(
		EXPORT, filters={"status": COMPLETED, "expires_at": ("<=", now_datetime())}, pluck="name"
	)
	for name in names:
		try:
			delete_files(name)
			frappe.db.set_value(EXPORT, name, "status", EXPIRED)
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"docarchive: export {name}")


def delete_files(name):
	for file in frappe.get_all(
		"File", filters={"attached_to_doctype": EXPORT, "attached_to_name": name}, pluck="name"
	):
		frappe.delete_doc("File", file, ignore_permissions=True)
	frappe.db.set_value("Archive Export Part", {"parent": name, "parenttype": EXPORT}, "file", None)
