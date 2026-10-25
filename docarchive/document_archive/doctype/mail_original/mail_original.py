import frappe
from frappe.model.document import Document


class MailOriginal(Document):
	def onload(self):
		self.raw_fallback = None


def on_doctype_update():
	frappe.db.add_index("Mail Original", ["email_account", "status", "ignored", "creation"])
