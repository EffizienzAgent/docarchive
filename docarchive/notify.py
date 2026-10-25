import frappe
from frappe.utils import today

from docarchive.permissions import ADMIN_ROLE


def recipients() -> list[str]:
	for role in (ADMIN_ROLE, "System Manager"):
		users = [
			user
			for user in frappe.get_all(
				"Has Role", filters={"role": role, "parenttype": "User"}, pluck="parent"
			)
			if user not in ("Administrator", "Guest") and frappe.db.get_value("User", user, "enabled")
		]
		if users:
			return sorted(set(users))
	return ["Administrator"]


def admins(subject, message, doctype=None, name=None) -> int:
	"""Bell notification to Archive Admins (System Managers if there are none), once per subject and day."""
	sent = 0
	for user in recipients():
		if frappe.db.exists(
			"Notification Log", {"for_user": user, "subject": subject, "creation": (">=", today())}
		):
			continue
		frappe.get_doc(
			{
				"doctype": "Notification Log",
				"for_user": user,
				"type": "Alert",
				"subject": subject,
				"email_content": message,
				"document_type": doctype,
				"document_name": name,
			}
		).insert(ignore_permissions=True)
		sent += 1
	return sent
