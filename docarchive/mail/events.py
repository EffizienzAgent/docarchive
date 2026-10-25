from functools import partial

import frappe

HOOK = "docarchive_mail_changed"


def changed(action: str, originals, communications=()) -> None:
	originals = list(originals)
	if originals:
		frappe.db.after_commit.add(partial(notify, action, originals, [c for c in communications if c]))


def notify(action, originals, communications):
	for method in methods():
		try:
			frappe.get_attr(method)(action=action, originals=originals, communications=communications)
		except Exception:
			frappe.log_error(title=f"docarchive mail changed hook: {method}", defer_insert=True)


def methods():
	return frappe.get_hooks(HOOK)
