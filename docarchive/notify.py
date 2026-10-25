from urllib.parse import quote

import frappe
from frappe import _
from frappe.utils import escape_html, today

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


ALERT_LINES = 50


def link_to(route, alert, **filters) -> str:
	query = "&".join(f"{key}={quote(str(value))}" for key, value in filters.items() if value)
	return f"/app/{quote(route)}?alert={quote(alert)}" + (f"&{query}" if query else "")


def lines(items, limit=ALERT_LINES) -> str:
	shown = [escape_html(item) for item in items[:limit]]
	if len(items) > limit:
		shown.append(escape_html(_("{0} more").format(len(items) - limit)))
	return "<br>".join(shown)


def admins(subject, message, link, once_a_day=True) -> int:
	"""Bell notification to Archive Admins (System Managers if there are none).

	The link opens the page to look at and names the condition. With once_a_day, each user gets
	one notification per link and day.
	"""
	sent = 0
	for user in recipients():
		if once_a_day and frappe.db.exists(
			"Notification Log", {"for_user": user, "link": link, "creation": (">=", today())}
		):
			continue
		frappe.get_doc(
			{
				"doctype": "Notification Log",
				"for_user": user,
				"type": "Alert",
				"subject": subject,
				"email_content": message,
				"link": link,
			}
		).insert(ignore_permissions=True)
		sent += 1
	return sent
