import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from docarchive import run, store
from docarchive.archive_now import names_of
from docarchive.permissions import ADMIN_ROLE

DOCUMENT = run.DOCUMENT
ON, OFF, FAILED = "On", "Off", "Failed"
SET, RELEASE = "Set", "Release"
MAX_NAMES = 200


def is_admin(user=None) -> bool:
	return ADMIN_ROLE in frappe.get_roles(user)


def clean_reason(reason) -> str:
	reason = (reason or "").strip()
	if not reason:
		frappe.throw(_("Please give a reason."))
	return reason


def checked_names(names) -> list[str]:
	names = names_of(names)
	if not names:
		frappe.throw(_("Please select at least one document."))
	if len(names) > MAX_NAMES:
		frappe.throw(_("Please select at most {0} documents.").format(MAX_NAMES))
	return names


def targets_by_name() -> dict:
	return {target.name: target for target in store.targets() or []}


def live_copies(doc) -> list:
	return [copy for copy in doc.archive_copies if copy.status == run.ARCHIVED]


def no_admin_access(target_name) -> str:
	return _("The admin key for storage target {0} is not configured.").format(target_name)


def admin_refusal(doc, by_name) -> str | None:
	for copy in doc.archive_copies:
		if copy.status == run.DELETED:
			continue
		target = by_name.get(copy.target_name)
		if not target or not store.has_admin_access(target):
			return no_admin_access(copy.target_name)
	return None


@frappe.whitelist()
def admin_access() -> bool:
	if not is_admin():
		return False
	return any(store.has_admin_access(target) for target in targets_by_name().values())


@frappe.whitelist(methods=["POST"])
def set_legal_hold(names, reason=None) -> dict:
	reason = clean_reason(reason)
	names = checked_names(names)
	for name in names:
		doc = frappe.get_doc(DOCUMENT, name)
		if not (is_admin() or doc.has_permission("write")):
			frappe.throw(_("You may not put {0} on legal hold.").format(name), frappe.PermissionError)
	by_name = targets_by_name()
	return {name: locked(name, hold_document, reason, by_name) for name in names}


@frappe.whitelist(methods=["POST"])
def release_legal_hold(names, reason=None, confirm=None) -> dict:
	frappe.only_for(ADMIN_ROLE)
	reason = clean_reason(reason)
	if not cint(confirm):
		frappe.throw(_("Please confirm the release."))
	names = checked_names(names)
	by_name = targets_by_name()
	for name in names:
		refusal = admin_refusal(frappe.get_doc(DOCUMENT, name), by_name)
		if refusal:
			frappe.throw(f"{name}: {refusal}")
	return {name: locked(name, release_document, reason, by_name) for name in names}


def locked(name, action, reason, by_name) -> dict:
	lock = run.document_lock(name)
	if not lock.acquire(blocking=False):
		return {"status": "busy", "message": _("The document is being processed. Please try again later.")}
	try:
		return action(frappe.get_doc(DOCUMENT, name), reason, by_name)
	finally:
		run.release(lock)


def hold_document(doc, reason, by_name) -> dict:
	copies = live_copies(doc)
	if not copies:
		return {"status": "refused", "message": _("Only archived documents can be put on legal hold.")}
	errors, refused = [], False
	for copy in copies:
		target = by_name.get(copy.target_name)
		if not target:
			copy.legal_hold = FAILED
			errors.append(f"{copy.target_name}: {not_configured()}")
			continue
		result = run.hold_copy(doc, copy, target)
		if result == run.HOLD_REFUSED:
			refused = True
			errors.append(f"{copy.target_name}: {hold_refused()}")
		elif result:
			errors.append(f"{copy.target_name}: {result}")
	held = any(copy.legal_hold == ON for copy in copies)
	if held:
		doc.update({"legal_hold": 1, "legal_hold_reason": reason})
	status = ("held" if not errors else "partial") if held else "refused"
	message = "\n".join(errors) or None
	add_log(doc, SET, reason, message or _("Legal hold is set on every copy."))
	run.save(doc)
	if refused and not held:
		message = hold_refused()
	return {"status": status, "message": message}


def not_configured() -> str:
	return _("The storage target is not configured.")


def hold_refused() -> str:
	return _("Setting a legal hold is not allowed by the storage right now.")


def release_document(doc, reason, by_name) -> dict:
	if not (doc.legal_hold or any(copy.legal_hold != OFF for copy in live_copies(doc))):
		return {"status": "refused", "message": _("The document is not under legal hold.")}
	errors = []
	for copy in live_copies(doc):
		if copy.legal_hold == OFF:
			continue
		target = by_name[copy.target_name]
		try:
			for key, version in run.copy_objects(copy, doc):
				store.release_hold(target, key, version)
			copy.legal_hold = OFF
		except Exception as e:
			copy.legal_hold = FAILED
			errors.append(f"{copy.target_name}: {run.safe_error(e)}")
			run.log(doc, target, run.safe_error(e))
	released = all(copy.legal_hold == OFF for copy in live_copies(doc))
	if released:
		doc.update({"legal_hold": 0, "legal_hold_reason": None})
	message = "\n".join(errors) or None
	add_log(doc, RELEASE, reason, message or _("Legal hold is released on every copy."))
	run.save(doc)
	return {"status": "released" if released else "partial", "message": message}


def add_log(doc, action, reason, result):
	doc.append(
		"legal_hold_log",
		{
			"action": action,
			"at": now_datetime(),
			"user": frappe.session.user,
			"reason": reason,
			"result": result,
		},
	)
