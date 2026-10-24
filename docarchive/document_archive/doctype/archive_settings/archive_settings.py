import frappe
from frappe import _
from frappe.model.document import Document

from docarchive import permissions, storage_targets


class ArchiveSettings(Document):
	def validate(self):
		self.validate_retention_rules()
		storage_targets.validate(self)

	def on_update(self):
		before = self.get_doc_before_save()
		permissions.sync_manager_roles(
			permissions.roles_of(self), removed=permissions.roles_of(before) - permissions.roles_of(self)
		)

	def validate_retention_rules(self):
		seen = set()
		for rule in self.retention_rules:
			if (rule.years or 0) <= 0:
				frappe.throw(_("Retention years must be positive."))
			key = (rule.own_company, rule.document_type)
			if key in seen:
				frappe.throw(_("There is more than one retention rule for {0} and {1}.").format(*key))
			seen.add(key)
