import itertools
import re

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, escape_html, get_datetime, now_datetime, today

from docarchive import notify, run, settings

DOCUMENT = run.DOCUMENT
REPORT = "Archive Completeness"
DEFAULT_OVERDUE_HOURS = 48
RUN_MISSED_HOURS = 26
SEEN_SECONDS = 26 * 3600
OVERDUE, FAILED, MISSING_COPIES, NUMBER_GAP = "Overdue", "Failed", "Missing Copies", "Number Gap"
MAIL_ORIGINAL = "Mail Original"
MAIL_STUCK_HOURS = 24
TRAILING_NUMBER = re.compile(r"^(.*?)(\d+)$")


def alert_link(condition, problem=None) -> str:
	"""Report link that names the condition; with a problem it also filters the report."""
	return notify.link_to(f"query-report/{REPORT}", condition, problem=problem)


def overdue_hours() -> int:
	value = settings.get().get("overdue_hours")
	return DEFAULT_OVERDUE_HOURS if value in (None, "") else cint(value)


def target_names() -> set[str]:
	from docarchive import store

	return {target.name for target in store.targets() or []}


def seen_key(condition, name) -> str:
	return f"docarchive:alerted:{today()}:{condition}:{name}"


def per_document(condition, names, subject, problem=None):
	"""Alert on documents not alerted for this condition today. Commits before marking them."""
	new = [name for name in dict.fromkeys(names) if not frappe.cache.get_value(seen_key(condition, name))]
	if not new:
		return
	notify.admins(
		subject.format(len(new)), notify.lines(new), alert_link(condition, problem), once_a_day=False
	)
	frappe.db.commit()
	for name in new:
		frappe.cache.set_value(seen_key(condition, name), 1, expires_in_sec=SEEN_SECONDS)


def archive_failed(names):
	per_document("failed", names, _("Archiving failed for {0} documents"), FAILED)


def run_failed(error):
	notify.admins(_("The nightly archive run failed"), escape_html(error), alert_link("run_failed"))


def after_run(summary):
	"""Called after a nightly run that got to the end."""
	frappe.db.set_single_value(
		"Archive Settings", "last_run_finished_at", now_datetime(), update_modified=False
	)
	frappe.db.commit()
	archive_failed(summary["failed"])
	per_document(
		"extension_failed", summary["extension_failed"], _("Extending the retention failed for {0} documents")
	)
	if summary["skipped"]:
		notify.admins(
			_("The archive run left {0} documents behind").format(len(summary["skipped"])),
			notify.lines(summary["skipped"]),
			alert_link("left_behind"),
		)
	check()


def check(names=None):
	names = target_names() if names is None else names
	if not names:
		notify.admins(
			_("Archiving is enabled, but no storage target is configured"), "", alert_link("not_configured")
		)
		frappe.db.commit()
		return
	state = State(names)
	late = [row["name"] for row in state.overdue()]
	if late:
		notify.admins(
			_("{0} documents are overdue for archiving").format(len(late)),
			notify.lines(late),
			alert_link("overdue", OVERDUE),
		)
	if state.missing:
		notify.admins(
			_("{0} documents have fewer copies than enabled storage targets").format(len(state.missing)),
			notify.lines(state.missing),
			alert_link("missing_copies", MISSING_COPIES),
		)
	frappe.db.commit()


def mail_originals() -> list[dict]:
	"""Failed originals and originals still pending after a day."""
	stuck = add_to_date(now_datetime(), hours=-MAIL_STUCK_HOURS)
	rows = frappe.get_all(
		"Mail Original",
		filters={"status": ("in", ("Failed", "Pending"))},
		fields=["name", "status", "subject", "error", "creation"],
		order_by="creation",
	)
	return [row for row in rows if row.status == "Failed" or get_datetime(row.creation) <= stuck]


def can_see_mail_originals() -> bool:
	"""Mail Originals have no own company, so users restricted to own companies do not see them."""
	from frappe.permissions import get_user_permissions

	return bool(frappe.has_permission("Mail Original", "read")) and not get_user_permissions(
		frappe.session.user
	).get("Own Company")


def mail_problem(row) -> str:
	return ": ".join(part for part in (row.name, _(row.status), first_line(row.error)) if part)


def check_mail_originals():
	rows = mail_originals()
	if rows:
		notify.admins(
			_("{0} e-mail originals could not be stored").format(len(rows)),
			notify.lines([mail_problem(row) for row in rows]),
			alert_link("mail_originals_failed", MAIL_ORIGINAL),
		)
		frappe.db.commit()


def daily():
	"""Scheduled check that also notices a nightly run that did not finish."""
	try:
		check_mail_originals()
	except Exception:
		frappe.log_error(title="docarchive monitoring: mail originals")
	if not settings.get().enabled:
		return
	last = frappe.db.get_single_value("Archive Settings", "last_run_finished_at")
	if target_names() and (
		not last or get_datetime(last) < add_to_date(now_datetime(), hours=-RUN_MISSED_HOURS)
	):
		notify.admins(
			_("The nightly archive run has not finished in the last {0} hours").format(RUN_MISSED_HOURS),
			escape_html(_("Last finished: {0}").format(last or _("never"))),
			alert_link("run_missed"),
		)
	check()


class State:
	"""Eligible and missing-copy documents, computed once per check."""

	def __init__(self, names):
		self.names = names
		self.missing = run.missing_copies(names)
		missing = set(self.missing)
		self.eligible = [name for name in run.eligible(names) if name not in missing]

	def overdue(self) -> list[dict]:
		"""Eligible documents that are not archived and waited longer than the overdue hours."""
		hours = overdue_hours()
		if not self.eligible or not hours:
			return []
		cutoff = add_to_date(now_datetime(), hours=-hours)
		rows = frappe.get_all(
			DOCUMENT,
			filters={"name": ("in", self.eligible), "archive_status": ("!=", run.FAILED)},
			fields=["name", "modified", "archive_started_at"],
		)
		return [
			{"name": row.name, "since": row.archive_started_at or row.modified}
			for row in rows
			if get_datetime(row.archive_started_at or row.modified) <= cutoff
		]


def first_line(text) -> str | None:
	return ((text or "").splitlines() or [""])[0][:140] or None


def problems(filters=None) -> list[dict]:
	"""Rows for the Archive Completeness report."""
	filters = frappe._dict(filters or {})
	names = target_names()
	state = State(names)
	found = {}
	for row in state.overdue():
		found[row["name"]] = (OVERDUE, row["since"], None)
	for row in frappe.get_all(
		DOCUMENT,
		filters={"archive_status": run.FAILED},
		fields=["name", "modified", "archive_started_at", "archive_error"],
	):
		found[row.name] = (FAILED, row.archive_started_at or row.modified, first_line(row.archive_error))
	held = {}
	for copy in frappe.get_all(
		"Archive Copy",
		filters={"parenttype": DOCUMENT, "status": run.ARCHIVED, "target_name": ("in", list(names) or [""])},
		fields=["parent", "target_name"],
	):
		held.setdefault(copy.parent, set()).add(copy.target_name)
	for name in state.missing:
		absent = ", ".join(sorted(names - held.get(name, set())))
		found[name] = (MISSING_COPIES, None, absent)
	rows = []
	if found:
		docs = frappe.get_list(
			DOCUMENT,
			filters=document_filters(filters, {"name": ("in", list(found))}),
			fields=["name", "title", "document_type", "own_company", "document_date", "document_number"],
			limit_page_length=0,
		)
		for doc in docs:
			problem, since, details = found[doc.name]
			rows.append({**doc, "problem": problem, "since": since, "details": details})
	if not (filters.document_type or filters.own_company) and can_see_mail_originals():
		rows += [
			{
				"name": None,
				"mail_original": row.name,
				"title": row.subject,
				"document_type": None,
				"own_company": None,
				"document_date": None,
				"document_number": None,
				"problem": MAIL_ORIGINAL,
				"since": row.creation,
				"details": mail_problem(row),
			}
			for row in mail_originals()
		]
	if filters.number_gaps:
		rows += number_gaps(filters)
	if filters.problem:
		rows = [row for row in rows if row["problem"] == filters.problem]
	return sorted(
		rows, key=lambda row: (row["problem"], str(row.get("document_date") or ""), row.get("name") or "")
	)


def document_filters(filters, base) -> dict:
	result = dict(base)
	for field in ("document_type", "own_company"):
		if filters.get(field):
			result[field] = filters[field]
	return result


def number_gaps(filters) -> list[dict]:
	"""Missing numbers between the lowest and highest number per type, own company and prefix."""
	types = frappe.get_all("Business Document Type", filters={"number_required": 1}, pluck="name")
	if filters.document_type:
		types = [filters.document_type]
	docs = frappe.get_list(
		DOCUMENT,
		filters=document_filters(
			filters,
			{
				"document_type": ("in", types or [""]),
				"document_number": ("is", "set"),
				"archive_status": ("!=", run.DELETED),
			},
		),
		fields=["document_type", "own_company", "document_number"],
		limit_page_length=0,
	)
	numbers = []
	for doc in docs:
		match = TRAILING_NUMBER.match(doc.document_number.strip())
		if match:
			numbers.append((doc.document_type, doc.own_company, *match.groups()))
	padded = {
		(document_type, own_company, prefix, len(digits))
		for document_type, own_company, prefix, digits in numbers
		if len(digits) > 1 and digits.startswith("0")
	}
	series = {}
	for document_type, own_company, prefix, digits in numbers:
		width = len(digits) if (document_type, own_company, prefix, len(digits)) in padded else 0
		series.setdefault((document_type, own_company, prefix, width), set()).add(int(digits))
	rows = []
	for (document_type, own_company, prefix, width), found in sorted(series.items(), key=str):
		ordered = sorted(found)
		for low, high in itertools.pairwise(ordered):
			if high - low > 1:
				first, last = f"{prefix}{str(low + 1).zfill(width)}", f"{prefix}{str(high - 1).zfill(width)}"
				rows.append(
					{
						"name": None,
						"title": None,
						"document_type": document_type,
						"own_company": own_company,
						"document_date": None,
						"document_number": first if first == last else f"{first} - {last}",
						"problem": NUMBER_GAP,
						"since": None,
						"details": _("{0} missing").format(high - low - 1),
					}
				)
	return rows


@frappe.whitelist()
def problem_count() -> int:
	if not frappe.has_permission(DOCUMENT, "read"):
		return 0
	return len(problems())


@frappe.whitelist()
def problem_count_card(filters=None) -> dict:
	return {"value": problem_count(), "fieldtype": "Int", "route": ["query-report", REPORT]}
