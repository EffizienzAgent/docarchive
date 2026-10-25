"""Audit log of state-changing archive events, kept under Object Lock.

API for other features:

    log(action, doctype=None, name=None, details=None, documents=None, write=False) -> entry name

- action: short dotted string, e.g. "legal_hold.set". See the constants below.
- doctype/name: the record that triggered the entry, if any (e.g. an Archive Export).
- details: JSON-serializable dict. No free text and no personal data: entries cannot be changed.
- documents: the documents the trigger touched (one entry per trigger, not per document), as
  tuples (doctype, name, outcome) or (doctype, name, outcome, reason) or dicts with these keys.
  outcome is a short word such as "archived", reason a short code such as "storage:AccessDenied".
- write: True when the caller does storage work in the same operation. The entry is then written
  to every enabled storage target right away. Otherwise a background job writes it.

Each entry becomes one locked object `_audit/<yyyy>/<mm>/<utc-timestamp>-<id>.json` on every
enabled target. The database row is a copy for the desk and a retry buffer. The nightly run writes
what is still missing and compares the database with each bucket.
"""

import hashlib
import json
import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import frappe
from frappe import _
from frappe.utils import cint, escape_html, get_datetime, now_datetime

from docarchive import notify, settings, store

LOG = "Archive Audit Log"
DOCUMENT_ROW = "Archive Audit Document"
COPY_ROW = "Archive Audit Copy"
DOCUMENT = "Business Document"
FORMAT = "docarchive-audit/1"
PREFIX = "_audit/"
SAVEPOINT = "docarchive_audit"
DEFAULT_YEARS = 10
UNWRITTEN_HOURS = 24
PENDING, WRITTEN, EXPIRED = "Pending", "Written", "Expired"
CHANGED_IN_DB = "changed in database"

ARCHIVE_RUN = "archive.run"
ARCHIVE_NOW = "archive.now"
LEGAL_HOLD_SET = "legal_hold.set"
LEGAL_HOLD_RELEASED = "legal_hold.released"
RETENTION_EXTENDED = "retention.extended"
DOCUMENTS_DELETED = "documents.deleted"
EXPORT_COMPLETED = "export.completed"
EXPORT_FAILED = "export.failed"
SETTINGS_CHANGED = "settings.changed"
DOCUMENT_CHANGED = "document.changed"


def utc(value) -> datetime:
	from docarchive.run import utc_iso

	return datetime.fromisoformat(utc_iso(get_datetime(value)).replace("Z", "+00:00"))


def utc_text(value) -> str:
	return utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def document_rows(documents) -> list[dict]:
	rows = []
	for item in documents or []:
		if isinstance(item, dict):
			row = {key: item.get(key) for key in ("doctype", "name", "outcome", "reason")}
		else:
			doctype, name, outcome, *rest = item
			row = {"doctype": doctype, "name": name, "outcome": outcome, "reason": rest[0] if rest else None}
		rows.append(row)
	return rows


def retain_until(at, years=None) -> datetime:
	years = years or cint(frappe.db.get_single_value("Archive Settings", "audit_retention_years"))
	return datetime(utc(at).year + (years or DEFAULT_YEARS) + 1, 1, 1, tzinfo=UTC)


def stored_until(value) -> datetime:
	return get_datetime(value).replace(tzinfo=UTC)


def object_key(entry_id, at) -> str:
	at = utc(at)
	return f"{PREFIX}{at:%Y}/{at:%m}/{at:%Y%m%dT%H%M%S%fZ}-{entry_id}.json"


def body_of(entry) -> str:
	return json.dumps(entry, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def stored_fields(entry) -> dict:
	"""Database fields derived from the stored entry body."""
	body = body_of(entry)
	return {
		"body": body,
		"sha256": hashlib.sha256(body.encode()).hexdigest(),
		"details": json.dumps(entry["details"], sort_keys=True, indent=1, ensure_ascii=False),
		"document_count": len(entry["documents"]),
	}


def document_row(row) -> dict:
	return {
		"document_doctype": row["doctype"],
		"document_name": row["name"],
		"outcome": row["outcome"],
		"reason": row["reason"],
	}


def log(action, doctype=None, name=None, details=None, documents=None, write=False) -> str:
	"""Record an audit entry and return its name. See the module docstring."""
	entry_id, at = uuid.uuid4().hex, now_datetime()
	rows = document_rows(documents)
	entry = {
		"format": FORMAT,
		"id": entry_id,
		"at": utc_text(at),
		"user": frappe.session.user,
		"action": action,
		"reference": {"doctype": doctype, "name": name} if doctype or name else None,
		"documents": rows,
		"details": details or {},
	}
	doc = frappe.get_doc(
		{
			"doctype": LOG,
			"at": at,
			"user": entry["user"],
			"action": action,
			"reference_doctype": doctype,
			"reference_name": name,
			**stored_fields(entry),
			"documents": [document_row(row) for row in rows],
			"object_key": object_key(entry_id, at),
			"retain_until": retain_until(at).replace(tzinfo=None),
			"status": PENDING,
		}
	)
	doc.flags.audit_write = True
	doc.insert(ignore_permissions=True, ignore_links=True)
	if write:
		frappe.db.after_commit.add(lambda: write_committed(doc.name))
	elif settings.get().enabled:
		frappe.enqueue(
			"docarchive.audit.flush",
			queue="short",
			job_id="docarchive-audit-flush",
			deduplicate=True,
			enqueue_after_commit=True,
		)
	return doc.name


def write_committed(name):
	"""Write an entry once its transaction is committed, so a rollback leaves no object behind."""
	if not frappe.db.exists(LOG, name):
		return
	frappe.db.savepoint(SAVEPOINT)
	try:
		write_entry(frappe.get_doc(LOG, name), enabled_targets())
		frappe.db.commit()
	except Exception:
		frappe.db.rollback(save_point=SAVEPOINT)
		frappe.log_error(title=f"docarchive: audit {name}")


def changes_anything(documents, unchanged=("skipped", "refused", "busy")) -> bool:
	return any(row["outcome"] not in unchanged for row in document_rows(documents))


def log_if_changed(action, documents, details=None, doctype=None, name=None) -> str | None:
	"""Log a trigger that did storage work, unless no document changed. Commits."""
	if not changes_anything(documents):
		return None
	entry = log(action, doctype, name, details, documents, write=True)
	frappe.db.commit()
	return entry


class Batch(list):
	"""Outcomes of a batch job. start() marks the document in progress, done() records its outcome."""

	current = None

	def start(self, doctype, name):
		self.current = (doctype, name)

	def done(self, doctype, name, outcome, reason=None):
		self.append((doctype, name, outcome, reason))
		self.current = None

	def details(self, details, aborted=False) -> dict:
		counts = Counter(row[2] for row in self)
		return {
			**(details or {}),
			"counts": dict(sorted(counts.items())),
			**({"aborted": True} if aborted else {}),
		}


@contextmanager
def batch(action, details=None):
	"""Log a batch job as one entry with the outcome per document and counts per outcome.

	An aborted batch still logs what it did, with details["aborted"], and the document it was
	working on as "interrupted".
	"""
	documents = Batch()
	try:
		yield documents
	except BaseException:
		frappe.db.rollback()
		if documents.current:
			documents.done(*documents.current, "interrupted")
		try:
			log_if_changed(action, documents, documents.details(details, aborted=True))
		except Exception:
			frappe.log_error(title=f"docarchive: audit {action}")
		raise
	log_if_changed(action, documents, documents.details(details))


def enabled_targets() -> list:
	if not settings.get().enabled:
		return []
	return store.targets() or []


def write_entry(doc, targets) -> bool:
	"""Write the entry to every target that does not hold it yet. Returns True if all hold it."""
	held = {copy.target_name for copy in doc.copies}
	until = stored_until(doc.retain_until)
	for target in targets:
		if target.name in held:
			continue
		try:
			version = put_entry(target, doc.object_key, doc.body.encode(), doc.sha256, until)
		except Exception as e:
			from docarchive.run import safe_error

			frappe.log_error(title=f"docarchive: audit {doc.name} ({target.name})", message=safe_error(e))
			continue
		doc.append(
			"copies", {"target_name": target.name, "version_id": version, "written_at": now_datetime()}
		)
		held.add(target.name)
	complete = bool(targets) and all(target.name in held for target in targets)
	doc.status = WRITTEN if complete else PENDING
	doc.flags.audit_write = True
	doc.flags.ignore_links = True
	doc.save(ignore_permissions=True)
	return complete


def put_entry(target, key, data, sha256, until) -> str:
	existing = store.head(target, key)
	if existing is None:
		version = store.put_locked(target, key, data, target.lock_mode, until, "application/json")[
			"version_id"
		]
	elif (existing.get("Metadata") or {}).get("sha256") == sha256:
		version = existing.get("VersionId")
	else:
		raise store.VerifyError(_("{0} already exists with different content.").format(key))
	store.verify(target, key, version, sha256, target.lock_mode, until)
	return version


def flush(targets=None) -> dict:
	"""Write pending entries to the enabled targets."""
	targets = enabled_targets() if targets is None else targets
	result = {"written": 0, "pending": 0}
	if not targets:
		return result
	for name in frappe.get_all(LOG, filters={"status": PENDING}, pluck="name", order_by="at asc"):
		frappe.db.savepoint(SAVEPOINT)
		try:
			done = write_entry(frappe.get_doc(LOG, name), targets)
			frappe.db.commit()
		except Exception:
			frappe.db.rollback(save_point=SAVEPOINT)
			frappe.log_error(title=f"docarchive: audit {name}")
			done = False
		result["written" if done else "pending"] += 1
	return result


def mark_new_targets(targets):
	"""Entries become pending again when a newly enabled target does not hold them yet."""
	names = {target.name for target in targets}
	held = {}
	for copy in frappe.get_all(COPY_ROW, fields=["parent", "target_name"], limit_page_length=0):
		held.setdefault(copy.parent, set()).add(copy.target_name)
	now = now_datetime()
	for row in frappe.get_all(
		LOG, filters={"status": WRITTEN, "retain_until": (">", now)}, pluck="name", limit_page_length=0
	):
		if not names <= held.get(row, set()):
			frappe.db.set_value(LOG, row, "status", PENDING, update_modified=False)


def reconcile(targets) -> dict:
	"""Compare the database copy with each bucket. Alerts Archive Admins on any mismatch."""
	now, cutoff = datetime.now(UTC), now_datetime() - timedelta(hours=UNWRITTEN_HOURS)
	entries = frappe.get_all(
		LOG,
		filters={"status": ("!=", EXPIRED)},
		fields=["name", "at", "user", "action", "body", "sha256", "object_key", "retain_until"],
		limit_page_length=0,
	)
	expired = set(frappe.get_all(LOG, filters={"status": EXPIRED}, pluck="object_key", limit_page_length=0))

	held = {}
	for copy in frappe.get_all(COPY_ROW, fields=["parent", "target_name"], limit_page_length=0):
		held.setdefault(copy.target_name, set()).add(copy.parent)
	mismatches = [
		{"target": None, "key": entry.object_key, "problem": CHANGED_IN_DB}
		for entry in entries
		if changed_in_db(entry)
	]
	for target in targets:
		try:
			listed = store.list_keys(target, PREFIX)
		except Exception as e:
			from docarchive.run import safe_error

			mismatches.append({"target": target.name, "key": PREFIX, "problem": safe_error(e)})
			continue
		for entry in entries:
			problem = target_problem(target, entry, listed, held.get(target.name, set()), now, cutoff)
			if problem:
				mismatches.append({"target": target.name, "key": entry.object_key, "problem": problem})
		known = {entry.object_key for entry in entries} | expired
		mismatches += [
			{"target": target.name, "key": key, "problem": "unknown"} for key in sorted(set(listed) - known)
		]
	if mismatches:
		alert(mismatches)
	return {"ok": not mismatches, "checked": len(entries), "mismatches": mismatches}


def target_problem(target, entry, listed, held, now, cutoff) -> str | None:
	if stored_until(entry.retain_until) <= now:
		return None
	if entry.name not in held:
		return "unwritten" if get_datetime(entry.at) < cutoff else None
	etag = listed.get(entry.object_key)
	if etag is None:
		return "missing"
	if etag != hashlib.md5(entry.body.encode(), usedforsecurity=False).hexdigest():
		try:
			found = store.head(target, entry.object_key)
		except Exception as e:
			from docarchive.run import safe_error

			return f"unchecked: {safe_error(e)}"
		if not found or (found.get("Metadata") or {}).get("sha256") != entry.sha256:
			return "differing"
	return None


def changed_in_db(entry) -> bool:
	if hashlib.sha256(entry.body.encode()).hexdigest() != entry.sha256:
		return True
	data = json.loads(entry.body)
	return (data["id"], data["user"], data["action"], data["at"], object_key(data["id"], data["at"])) != (
		entry.name,
		entry.user,
		entry.action,
		utc_text(entry.at),
		entry.object_key,
	)


def alert(mismatches):
	lines = [f"{m['target'] or _('Database')}: {m['key']} ({m['problem']})" for m in mismatches]
	notify.admins(
		_("The audit log does not match the storage"),
		notify.lines(lines),
		notify.link_to("archive-audit-log", "reconcile"),
	)


def nightly(targets) -> dict:
	mark_new_targets(targets)
	return {**flush(targets), **reconcile(targets)}


def prune() -> int:
	"""Daily: blank entries whose lock has ended. User, content and document references go; the
	row keeps time, action and object key, so reconcile still knows the object until a lifecycle
	rule removes it."""
	now = datetime.now(UTC).replace(tzinfo=None)
	names = frappe.get_all(
		LOG,
		filters={"retain_until": ("<=", now), "status": ("!=", EXPIRED)},
		pluck="name",
		limit_page_length=0,
	)
	for start in range(0, len(names), 500):
		chunk = names[start : start + 500]
		frappe.db.delete(DOCUMENT_ROW, {"parenttype": LOG, "parent": ("in", chunk)})
		frappe.db.set_value(
			LOG,
			{"name": ("in", chunk)},
			{
				**dict.fromkeys(("user", "reference_doctype", "reference_name", "details", "body", "sha256")),
				"document_count": 0,
				"status": EXPIRED,
			},
			update_modified=False,
		)
	frappe.db.commit()
	return len(names)


def note_change(doctype, name, fields):
	"""Log a pre-archive change. Further changes in the same transaction by the same user extend
	that entry, so a bulk update gives one entry and one locked object."""
	open_entry = getattr(frappe.local, "docarchive_change_entry", None)
	if (
		open_entry
		and open_entry[1] == frappe.session.user
		and extend_change(open_entry[0], doctype, name, fields)
	):
		return
	entry = log(
		DOCUMENT_CHANGED,
		details={"fields": {name: sorted(fields)}},
		documents=[(doctype, name, "changed")],
	)
	frappe.local.docarchive_change_entry = (entry, frappe.session.user)
	frappe.db.after_commit.add(close_change_entry)
	frappe.db.after_rollback.add(close_change_entry)


def close_change_entry():
	frappe.local.docarchive_change_entry = None


def extend_change(entry_name, doctype, name, fields) -> bool:
	"""Add a document to an entry of this transaction that no bucket holds yet."""
	row = frappe.db.get_value(LOG, entry_name, ["body", "status"], as_dict=True)
	if not row or row.status != PENDING or frappe.db.exists(COPY_ROW, {"parent": entry_name}):
		return False
	entry = json.loads(row.body)
	if any(item["doctype"] != doctype for item in entry["documents"]):
		return False
	changed = entry["details"]["fields"]
	if name not in changed:
		item = {"doctype": doctype, "name": name, "outcome": "changed", "reason": None}
		entry["documents"].append(item)
		frappe.get_doc(
			{
				"doctype": DOCUMENT_ROW,
				"parenttype": LOG,
				"parentfield": "documents",
				"parent": entry_name,
				"idx": len(entry["documents"]),
				**document_row(item),
			}
		).db_insert()
	changed[name] = sorted(set(changed.get(name, [])) | set(fields))
	frappe.db.set_value(LOG, entry_name, stored_fields(entry), update_modified=False)
	return True


@frappe.whitelist()
def entries_for(doctype: str, name: str) -> list[dict]:
	"""Audit entries that touched a document the user can read, newest first."""
	frappe.has_permission(doctype, "read", name, throw=True)
	rows = frappe.get_all(
		DOCUMENT_ROW,
		filters={"document_doctype": doctype, "document_name": name, "parenttype": LOG},
		fields=["parent", "outcome", "reason"],
		limit_page_length=0,
	)
	if not rows:
		return []
	by_entry = {row.parent: row for row in rows}
	entries = frappe.get_all(
		LOG,
		filters={"name": ("in", list(by_entry))},
		fields=["name", "at", "user", "action", "status"],
		order_by="at desc",
	)
	return [
		frappe._dict(entry, outcome=by_entry[entry.name].outcome, reason=by_entry[entry.name].reason)
		for entry in entries
	]


def entries_for_export(names=(), date_from=None, date_to=None) -> list:
	"""Entries that touched one of the documents, and entries without documents (settings changes)
	between the dates, oldest first."""
	names, found = list(names), set()
	for start in range(0, len(names), 500):
		found.update(
			frappe.get_all(
				DOCUMENT_ROW,
				filters={"document_doctype": DOCUMENT, "document_name": ("in", names[start : start + 500])},
				pluck="parent",
				limit_page_length=0,
			)
		)
	if date_from or date_to:
		low = f"{date_from} 00:00:00" if date_from else "1900-01-01 00:00:00"
		high = f"{date_to} 23:59:59.999999" if date_to else "9999-12-31 23:59:59"
		found.update(
			frappe.get_all(
				LOG,
				filters={"at": ("between", [low, high]), "document_count": 0},
				pluck="name",
				limit_page_length=0,
			)
		)
	if not found:
		return []
	return frappe.get_all(
		LOG,
		filters={"name": ("in", list(found)), "status": ("!=", EXPIRED)},
		fields=["name", "at", "body", "sha256", "object_key", "status"],
		order_by="at asc",
		limit_page_length=0,
	)


def changed_fields(before, after, fields, tables=()) -> list[str]:
	"""Names of the fields and tables that changed. Values stay out of the entry."""
	changed = [field for field in fields if (before.get(field) if before else None) != after.get(field)]
	for table, columns in tables:
		old = rows_of(before, table, columns) if before else []
		if old != rows_of(after, table, columns):
			changed.append(table)
	return changed


def rows_of(doc, table, columns) -> list:
	return [{column: row.get(column) for column in columns} for row in doc.get(table) or []]


def log_settings(doc, fields, tables=()):
	before = doc.get_doc_before_save()
	changed = changed_fields(before, doc, fields, tables)
	if changed:
		log(SETTINGS_CHANGED, doc.doctype, doc.name, {"fields": changed, "created": before is None})
