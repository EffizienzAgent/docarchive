import frappe
from frappe import _

from docarchive import counterparty

DOCUMENT = "Business Document"
FILE_LINK_FIELDS = ("file_url", "is_private", "attached_to_doctype", "attached_to_name", "attached_to_field")


def archive_started(filters):
	return frappe.db.exists(DOCUMENT, {**filters, "archive_started_at": ("is", "set")})


def archived_file(file_doc) -> bool:
	return bool(
		file_doc.attached_to_doctype == DOCUMENT
		and file_doc.attached_to_name
		and archive_started({"name": file_doc.attached_to_name})
	)


def protect_file(file_doc, *args, **kwargs):
	if archived_file(file_doc):
		frappe.throw(_("This file belongs to an archived document and cannot be deleted."))


def protect_file_change(file_doc, method=None):
	before = file_doc.get_doc_before_save()
	if before and archived_file(before) and any(file_doc.get(f) != before.get(f) for f in FILE_LINK_FIELDS):
		frappe.throw(_("This file belongs to an archived document and cannot be changed."))


def file_permission(doc, ptype=None, user=None, debug=False) -> bool:
	return not (ptype == "delete" and archived_file(doc))


def protect_party(doc, method=None, old=None, new=None, merge=False):
	allowed = counterparty.doctypes()
	if allowed and doc.doctype not in allowed:
		return
	name = old or doc.name
	if archive_started({"counterparty_type": doc.doctype, "counterparty": name}):
		frappe.throw(_("{0} is linked from archived documents and cannot be renamed or merged.").format(name))
