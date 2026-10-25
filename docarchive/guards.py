import frappe
from frappe import _

from docarchive import counterparty
from docarchive.permissions import ADMIN_ROLE

DOCUMENT = "Business Document"
EXPORT = "Archive Export"
DELETED = "Deleted"
FILE_LINK_FIELDS = ("file_url", "is_private", "attached_to_doctype", "attached_to_name", "attached_to_field")
OBJECT_FIELDS = ("file_url", "content_hash", "thumbnail_url", "is_private")
URL_PREFIXES = {"private/files/": "/private/files/", "public/files/": "/files/"}


def archive_started(filters):
	return frappe.db.exists(
		DOCUMENT, {**filters, "archive_started_at": ("is", "set"), "archive_status": ("!=", DELETED)}
	)


def archived_rows(**filters) -> list[frappe._dict]:
	rows = frappe.get_all(
		"File",
		filters={**filters, "attached_to_doctype": DOCUMENT},
		fields=["file_url", "content_hash", "attached_to_name"],
	)
	if not rows:
		return []
	started = set(
		frappe.get_all(
			DOCUMENT,
			filters={
				"name": ("in", [r.attached_to_name for r in rows]),
				"archive_started_at": ("is", "set"),
				"archive_status": ("!=", DELETED),
			},
			pluck="name",
		)
	)
	return [r for r in rows if r.attached_to_name in started]


def archived_url(file_url) -> list[frappe._dict]:
	if not file_url:
		return []
	return [r for r in archived_rows(file_url=file_url) if r.file_url == file_url]


def archived_hash(content_hash) -> bool:
	return bool(content_hash and archived_rows(content_hash=content_hash))


def shares_archive(file_doc) -> bool:
	return bool(
		archived_url(file_doc.file_url)
		or archived_hash(file_doc.content_hash)
		or archived_url(file_doc.thumbnail_url)
	)


def archived_file(file_doc) -> bool:
	return bool(
		file_doc.attached_to_doctype == DOCUMENT
		and file_doc.attached_to_name
		and archive_started({"name": file_doc.attached_to_name})
	)


def refuse_delete():
	frappe.throw(_("This file belongs to an archived document and cannot be deleted."))


def refuse_change():
	frappe.throw(_("This file belongs to an archived document and cannot be changed."))


def protect_file(file_doc, *args, **kwargs):
	if archived_file(file_doc) or archived_url(file_doc.thumbnail_url):
		refuse_delete()
	if any(row.content_hash != file_doc.content_hash for row in archived_url(file_doc.file_url)):
		refuse_delete()


def protect_object(file_url=None, key=None, **kwargs):
	if key and not file_url:
		file_url = url_for_key(key)
	if archived_url(file_url):
		frappe.throw(_("Archived files cannot be changed."))


def url_for_key(key):
	for prefix, url in URL_PREFIXES.items():
		if key.startswith(prefix):
			return url + key[len(prefix) :]
	return None


def protect_file_change(file_doc, method=None):
	before = file_doc.get_doc_before_save()
	if not before:
		protect_new_file(file_doc)
		return
	if archived_file(before) and changed(file_doc, before, FILE_LINK_FIELDS):
		refuse_change()
	if (shares_archive(before) or shares_archive(file_doc)) and changed(file_doc, before, OBJECT_FIELDS):
		refuse_change()


def protect_new_file(file_doc):
	if archived_url(file_doc.thumbnail_url):
		refuse_change()
	rows = archived_url(file_doc.file_url)
	if file_doc.content_hash and any(row.content_hash != file_doc.content_hash for row in rows):
		refuse_change()


def changed(doc, before, fields) -> bool:
	return any(doc.get(field) != before.get(field) for field in fields)


def file_permission(doc, ptype=None, user=None, debug=False) -> bool:
	if doc.attached_to_doctype == EXPORT:
		return ADMIN_ROLE in frappe.get_roles(user)
	if ptype == "delete" and archived_file(doc):
		return False
	if (
		ptype in ("write", "delete", "create")
		and doc.attached_to_doctype == DOCUMENT
		and doc.attached_to_name
	):
		return bool(frappe.has_permission(DOCUMENT, "write", doc.attached_to_name, user=user))
	return True


def protect_party(doc, method=None, old=None, new=None, merge=False):
	allowed = counterparty.doctypes()
	if allowed and doc.doctype not in allowed:
		return
	name = old or doc.name
	if archive_started({"counterparty_type": doc.doctype, "counterparty": name}):
		frappe.throw(_("{0} is linked from archived documents and cannot be renamed or merged.").format(name))
