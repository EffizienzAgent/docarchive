import frappe
from frappe import _
from frappe.model.document import Document

from docarchive import content, export_package, permissions, storage_targets


class ArchiveSettings(Document):
	def validate(self):
		self.granted_permissions = frappe.db.get_single_value("Archive Settings", "granted_permissions")
		self.validate_retention_rules()
		self.validate_mail_accounts()
		permissions.validate_roles(self)
		storage_targets.validate(self)
		self.validate_export_brand()

	def on_update(self):
		permissions.sync_manager_roles(permissions.roles_of(self))

	def validate_export_brand(self):
		self.export_brand_color = (self.export_brand_color or "").strip() or None
		if self.export_brand_color and not export_package.valid_color(self.export_brand_color):
			frappe.throw(_("The brand color must be a hex color like #1a7f37."))
		if self.export_brand_logo:
			error = export_package.logo_error(
				self.export_brand_logo, content.read_bytes(self.export_brand_logo)
			)
			if error:
				frappe.throw(error)

	def validate_retention_rules(self):
		seen = set()
		for rule in self.retention_rules:
			if (rule.years or 0) <= 0:
				frappe.throw(_("Retention years must be positive."))
			key = (rule.own_company, rule.document_type)
			if key in seen:
				frappe.throw(_("There is more than one retention rule for {0} and {1}.").format(*key))
			seen.add(key)

	def validate_mail_accounts(self):
		seen = set()
		for row in self.mail_accounts:
			if row.email_account in seen:
				frappe.throw(_("E-mail account {0} is listed more than once.").format(row.email_account))
			if (row.keep_days or 0) < 1:
				frappe.throw(_("Keep days must be at least 1."))
			self.validate_sent_folder(row)
			seen.add(row.email_account)

	def validate_sent_folder(self, row):
		if row.outgoing_source == "Sent Folder" and not frappe.db.get_value(
			"Email Account", row.email_account, "use_imap"
		):
			frappe.throw(
				_("{0} does not use IMAP, so its sent mails cannot be read from the Sent folder.").format(
					row.email_account
				)
			)
