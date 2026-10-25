import frappe
from frappe import _
from frappe.model.document import Document


class DocumentDeletionLog(Document):
	def validate(self):
		if not self.is_new():
			frappe.throw(_("Deletion log entries cannot be changed."))

	def on_trash(self):
		frappe.throw(_("Deletion log entries cannot be deleted."))
