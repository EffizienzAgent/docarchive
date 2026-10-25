import json

import frappe
from frappe import _
from frappe.model.document import Document

FIXED = (
	"at",
	"user",
	"action",
	"reference_doctype",
	"reference_name",
	"details",
	"body",
	"sha256",
	"object_key",
)


class ArchiveAuditLog(Document):
	def autoname(self):
		self.name = json.loads(self.body)["id"]

	def validate(self):
		if not self.flags.audit_write:
			frappe.throw(_("Audit log entries are written by the system and cannot be changed."))
		before = self.get_doc_before_save()
		if before and (
			any(str(self.get(f) or "") != str(before.get(f) or "") for f in FIXED)
			or rows(self, "documents") != rows(before, "documents")
			or not rows(before, "copies") <= rows(self, "copies")
		):
			frappe.throw(_("Audit log entries cannot be changed."))

	def on_trash(self):
		frappe.throw(_("Audit log entries cannot be deleted."))

	def before_rename(self, old, new, merge=False):
		frappe.throw(_("Audit log entries cannot be renamed."))


def rows(doc, table) -> set:
	fields = ("document_doctype", "document_name", "outcome", "reason", "target_name", "version_id")
	return {tuple(str(row.get(f) or "") for f in fields) for row in doc.get(table) or []}
