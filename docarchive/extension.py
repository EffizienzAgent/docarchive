import hashlib
from datetime import UTC, datetime, time, timedelta

import frappe
from frappe import _
from frappe.utils import getdate, now_datetime

from docarchive import audit, permissions, retention, run, settings, store

DOCUMENT = run.DOCUMENT
EVENT = "docarchive_extension"
ADD_YEARS = "add_years"
AT_LEAST = "at_least"
TO_DATE = "to_date"
MAX_YEARS = 100


class ExtensionError(frappe.ValidationError):
	pass


def is_manager() -> bool:
	return bool(set(frappe.get_roles()) & {*permissions.manager_roles(), "System Manager"})


def check_manager():
	if not is_manager():
		frappe.throw(_("Only document managers can extend the retention."), frappe.PermissionError)


def archived_copies(doc):
	return [copy for copy in doc.archive_copies if copy.status == run.ARCHIVED and copy.retain_until]


def current_until(doc) -> datetime | None:
	dates = [retention.from_db(copy.retain_until) for copy in archived_copies(doc)]
	dates += [retention.from_db(doc.retain_until), retention.from_db(doc.extension_until)]
	dates = [date for date in dates if date]
	return max(dates) if dates else None


def years_value(years) -> int:
	try:
		years = int(years)
	except TypeError, ValueError:
		years = 0
	if not 1 <= years <= MAX_YEARS:
		frappe.throw(_("Choose between 1 and {0} years.").format(MAX_YEARS), ExtensionError)
	return years


def add_years(value, years):
	try:
		return value.replace(year=value.year + years)
	except ValueError:
		return value.replace(year=value.year + years, day=28)


def latest_allowed() -> datetime:
	return add_years(datetime.now(UTC), MAX_YEARS)


def new_until(doc, how, years=None, until=None) -> datetime:
	if how == ADD_YEARS:
		result = add_years(current_until(doc), years_value(years))
	elif how == AT_LEAST:
		result = max(current_until(doc), retention.retain_until(doc, years_value(years)))
	elif how == TO_DATE and until:
		result = datetime.combine(getdate(until) + timedelta(days=1), time(), UTC)
	else:
		frappe.throw(_("Choose how to extend the retention."), ExtensionError)
	if result > latest_allowed():
		frappe.throw(_("The new date can be at most {0} years from today.").format(MAX_YEARS), ExtensionError)
	return result


def is_deleted(doc) -> bool:
	return doc.archive_status == run.DELETED or any(copy.status == run.DELETED for copy in doc.archive_copies)


def missing_targets(doc, by_name) -> list[str]:
	return sorted({copy.target_name for copy in archived_copies(doc) if copy.target_name not in by_name})


def refusal(doc, until) -> str | None:
	if is_deleted(doc):
		return _("Deleted documents cannot be extended.")
	if doc.archive_status != run.ARCHIVED or not archived_copies(doc):
		return _("Only archived documents can be extended.")
	if doc.extension_until:
		return _("An extension of this document is still running. Please try again after the nightly run.")
	if until <= current_until(doc):
		return _("The new date must be later than the current retention ({0}).").format(
			run.utc_iso(current_until(doc))
		)
	return None


def check(name):
	doc = frappe.get_doc(DOCUMENT, name)
	doc.check_permission("write")
	return doc


def documents(names=None, counterparty_type=None, counterparty=None) -> list[str]:
	if counterparty_type and counterparty:
		frappe.has_permission(counterparty_type, "read", counterparty, throw=True)
		return frappe.get_list(
			DOCUMENT,
			filters={
				"counterparty_type": counterparty_type,
				"counterparty": counterparty,
				"archive_status": run.ARCHIVED,
			},
			pluck="name",
			order_by="document_date desc",
		)
	names = frappe.parse_json(names) if isinstance(names, str) and names.startswith("[") else names
	return list(dict.fromkeys([names] if isinstance(names, str) else names or []))


def plan_row(name, how, years, until) -> dict:
	doc = check(name)
	current = current_until(doc)
	row = {"name": doc.name, "title": doc.title, "current": run.utc_iso(current) if current else None}
	reason = None if current else _("Only archived documents can be extended.")
	if not reason:
		target = new_until(doc, how, years, until)
		reason = refusal(doc, target)
		row["new"] = run.utc_iso(target)
	row.update(skipped=reason, modes=sorted({copy.lock_mode for copy in archived_copies(doc)}))
	return row


def targets_by_name():
	targets = store.targets() if settings.get().enabled else None
	if not targets:
		frappe.throw(_("No storage target is configured."), ExtensionError)
	return {target.name: target for target in targets}


def check_extendable(doc, until, by_name):
	refused = refusal(doc, until)
	if refused:
		frappe.throw(refused, ExtensionError)
	missing = missing_targets(doc, by_name)
	if missing:
		frappe.throw(
			_("These storage targets hold a copy but are not enabled: {0}").format(", ".join(missing)),
			ExtensionError,
		)


def apply(doc, until, reason=None) -> bool:
	by_name = targets_by_name()
	check_extendable(doc, until, by_name)
	lock = run.document_lock(doc.name)
	if not lock.acquire(blocking=False):
		frappe.throw(_("This document is being archived right now. Please try again later."), ExtensionError)
	try:
		doc.reload()
		check_extendable(doc, until, by_name)
		note = {
			"kind": "manual",
			"extended_by": frappe.session.user,
			"reason": (reason or "").strip() or None,
		}
		run.prepare_extension(doc, until, note)
		return run.extend_document(doc, until, by_name)
	finally:
		run.release(lock)


@frappe.whitelist(methods=["POST"])
def options(name: str) -> dict:
	doc = frappe.get_doc(DOCUMENT, name)
	doc.check_permission("read")
	allowed = (
		is_manager()
		and doc.has_permission("write")
		and doc.archive_status == run.ARCHIVED
		and not is_deleted(doc)
	)
	current = current_until(doc)
	return {
		"allowed": bool(allowed and archived_copies(doc) and not doc.extension_until),
		"pending": run.utc_iso(retention.from_db(doc.extension_until)) if doc.extension_until else None,
		"current": run.utc_iso(current) if current else None,
		"copies": [
			{
				"target": copy.target_name,
				"mode": copy.lock_mode,
				"retain_until": run.utc_iso(retention.from_db(copy.retain_until)),
			}
			for copy in archived_copies(doc)
		],
	}


@frappe.whitelist(methods=["POST"])
def extend(name: str, how: str, years=None, until=None, reason=None) -> dict:
	check_manager()
	doc = check(name)
	if not current_until(doc):
		frappe.throw(_("Only archived documents can be extended."), ExtensionError)
	target = new_until(doc, how, years, until)
	done = apply(doc, target, reason)
	audit.log_if_changed(
		audit.RETENTION_EXTENDED, [(DOCUMENT, doc.name, "extended" if done else "pending")], {"how": how}
	)
	return {"done": done, "retain_until": run.utc_iso(target)}


@frappe.whitelist(methods=["POST"])
def preview(how: str, years=None, until=None, names=None, counterparty_type=None, counterparty=None) -> dict:
	check_manager()
	rows = [plan_row(name, how, years, until) for name in documents(names, counterparty_type, counterparty)]
	return {"rows": rows, "count": sum(1 for row in rows if not row["skipped"])}


@frappe.whitelist(methods=["POST"])
def extend_many(
	how: str, years=None, until=None, reason=None, names=None, counterparty_type=None, counterparty=None
) -> dict:
	check_manager()
	targets_by_name()
	names = documents(names, counterparty_type, counterparty)
	for name in names:
		check(name)
	if how != TO_DATE:
		years_value(years)
	key = frappe.as_json({"names": sorted(names), "how": how, "years": years, "until": until})
	job_id = "docarchive-extend-" + hashlib.sha256(key.encode()).hexdigest()[:16]
	frappe.enqueue(
		"docarchive.extension.run_many",
		queue="long",
		job_id=job_id,
		deduplicate=True,
		enqueue_after_commit=True,
		names=names,
		how=how,
		years=years,
		until=until,
		reason=reason,
		user=frappe.session.user,
	)
	return {"job_id": job_id, "total": len(names)}


def run_many(names, how, years=None, until=None, reason=None, user=None):
	result = {"extended": [], "pending": [], "skipped": {}, "failed": {}}
	with audit.batch(audit.RETENTION_EXTENDED, {"how": how}) as documents:
		for index, name in enumerate(names, 1):
			documents.start(DOCUMENT, name)
			try:
				doc = check(name)
				target = new_until(doc, how, years, until) if current_until(doc) else None
				skipped = refusal(doc, target) if target else _("Only archived documents can be extended.")
				outcome = None if skipped else "extended" if apply(doc, target, reason) else "pending"
				frappe.db.commit()
			except Exception as error:
				frappe.db.rollback()
				result["failed"][name] = run.safe_error(error)
				documents.done(DOCUMENT, name, "failed", run.reason_code(error))
			else:
				if skipped:
					result["skipped"][name] = skipped
					documents.done(DOCUMENT, name, "skipped", "refused")
				else:
					result[outcome].append(name)
					documents.done(DOCUMENT, name, outcome)
			frappe.publish_realtime(EVENT, {"done": index, "total": len(names)}, user=user)
	frappe.publish_realtime(EVENT, {"done": len(names), "total": len(names), "result": result}, user=user)
	return result


def ending_filters(days=90) -> list:
	limit = frappe.utils.add_days(now_datetime(), int(days))
	return [["archive_status", "=", run.ARCHIVED], ["retain_until", "<=", limit]]


@frappe.whitelist()
def count_ending(days=90) -> int:
	if not frappe.has_permission(DOCUMENT, "read"):
		return 0
	return len(frappe.get_list(DOCUMENT, filters=ending_filters(days), pluck="name", limit_page_length=0))


@frappe.whitelist()
def ending_card(filters=None) -> dict:
	return {"value": count_ending(90), "fieldtype": "Int"}
