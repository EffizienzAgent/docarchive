import frappe
from frappe import _

LOCK_SECONDS = 900
WAIT_SECONDS = 30


class Busy(frappe.ValidationError):
	http_status_code = 409


def hold(*keys):
	"""Lock the keys until the current transaction commits or rolls back.

	Reads after hold() must lock rows (for_update) to see what another holder committed."""
	held = _held()
	for key in sorted(set(keys) - set(held)):
		lock = frappe.cache.lock(
			frappe.cache.make_key(f"docarchive:{key}"), timeout=LOCK_SECONDS, blocking_timeout=WAIT_SECONDS
		)
		if not lock.acquire():
			frappe.throw(_("Another request is filing the same document. Please try again."), Busy)
		if not held:
			frappe.db.after_commit.add(release_all)
			frappe.db.after_rollback.add(release_all)
		held[key] = lock


def release_all():
	held = _held()
	for lock in held.values():
		try:
			lock.release()
		except Exception:
			pass
	held.clear()


def _held() -> dict:
	if not hasattr(frappe.local, "docarchive_locks"):
		frappe.local.docarchive_locks = {}
	return frappe.local.docarchive_locks
