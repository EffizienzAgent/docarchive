import uuid
from unittest.mock import patch

import frappe

from docarchive.permissions import MANAGER_ROLE


def unique(prefix):
	return f"{prefix}-{uuid.uuid4().hex[:8]}"


def pdf(marker=b""):
	objects = [
		b"<< /Type /Catalog /Pages 2 0 R >>",
		b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
		b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>",
	]
	out = b"%PDF-1.7\n%\x81\x9d\xff\xfe" + marker + b"\n"
	offsets = []
	for number, body in enumerate(objects, 1):
		offsets.append(len(out))
		out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
	xref = len(out)
	out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
	out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
	out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
	return out


PDF = pdf()


def read_from_store(file_url):
	return b"stored:" + file_url.encode()


def make_own_company(slug="acme-gmbh", **fields):
	slug = unique(slug)
	return frappe.get_doc(
		{"doctype": "Own Company", "slug": slug, "company_name": slug.upper(), **fields}
	).insert(ignore_permissions=True)


def make_settings(rules=(), targets=None, manager_roles=None, **fields):
	settings = frappe.get_single("Archive Settings")
	settings.update(
		{
			"enabled": 0,
			"min_age_hours": 24,
			"default_own_company": None,
			"successor_own_company": None,
			"successor_from_date": None,
			"max_future_days": 31,
			"max_contract_years": 50,
			"overdue_hours": 48,
			**fields,
		}
	)
	settings.set("retention_rules", [])
	for own_company, document_type, years in rules:
		settings.append(
			"retention_rules", {"own_company": own_company, "document_type": document_type, "years": years}
		)
	if targets is not None:
		settings.set("storage_targets", [dict(target) for target in targets])
	if manager_roles is not None:
		settings.set("manager_roles", [{"role": role} for role in manager_roles])
	settings.save(ignore_permissions=True)
	return settings


def site_config_target(name="primary", **fields):
	return {
		"target_name": name,
		"enabled": 1,
		"lock_mode": "GOVERNANCE",
		"config_source": "Site Config",
		"config_prefix": f"archive_{name}",
		"access_key_config_key": "archive_access_key",
		"secret_key_config_key": "archive_secret_key",
		**fields,
	}


def fields_target(name="direct", **fields):
	return {
		"target_name": name,
		"enabled": 1,
		"lock_mode": "GOVERNANCE",
		"config_source": "Fields",
		"endpoint": "https://s3.example.test",
		"region": "eu-test-1",
		"bucket": f"archive-{name}",
		"access_key": "key",
		"secret_key": "secret",
		**fields,
	}


def make_file(content=None, file_name=None, is_private=1, **fields):
	return frappe.get_doc(
		{
			"doctype": "File",
			"file_name": file_name or f"{unique('receipt')}.pdf",
			"content": content or pdf(uuid.uuid4().hex.encode()),
			"is_private": is_private,
			**fields,
		}
	).insert(ignore_permissions=True)


def make_document_type(**fields):
	slug = unique("type")
	return frappe.get_doc(
		{
			"doctype": "Business Document Type",
			"type_name": slug.title(),
			"slug": slug,
			"archive": 1,
			"default_retention_years": 8,
			"counterparty_required": 1,
			**fields,
		}
	).insert(ignore_permissions=True)


def make_party(first_name="Acme"):
	return frappe.get_doc({"doctype": "Contact", "first_name": unique(first_name)}).insert(
		ignore_permissions=True
	)


def party_fields(party):
	return {"counterparty_type": party.doctype, "counterparty": party.name}


def make_business_document(archive_write=False, **fields):
	if "file" not in fields:
		fields["file"] = make_file().file_url
	if "document_type" not in fields:
		fields["document_type"] = make_document_type().name
	doc = frappe.get_doc(
		{
			"doctype": "Business Document",
			"document_date": "2026-03-15",
			"document_number": "INV-2026-017",
			**fields,
		}
	)
	doc.flags.archive_write = archive_write
	return doc.insert(ignore_permissions=True)


def make_user(email, roles=()):
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0}
		).insert(ignore_permissions=True)
	user = frappe.get_doc("User", email)
	missing = set(roles) - {r.role for r in user.roles}
	if missing:
		for role in sorted(missing):
			user.append("roles", {"role": role})
		user.save(ignore_permissions=True)
	return user.name


def make_role(name):
	if not frappe.db.exists("Role", name):
		frappe.get_doc({"doctype": "Role", "role_name": name, "desk_access": 1}).insert(
			ignore_permissions=True
		)
	return name


def make_manager(email):
	return make_user(email, [MANAGER_ROLE])


def make_outsider(email):
	return make_user(email, [make_role("Archive Outsider")])


def make_email_account(email_id=None, **fields):
	email_id = email_id or f"{unique('info')}@acme.test"
	return frappe.get_doc(
		{
			"doctype": "Email Account",
			"email_account_name": unique("account"),
			"email_id": email_id,
			"enable_incoming": 0,
			"enable_outgoing": 0,
			**fields,
		}
	).insert(ignore_permissions=True)


def make_mail_settings(email_account, **fields):
	settings = frappe.get_single("Archive Settings")
	settings.set("mail_accounts", [])
	settings.append("mail_accounts", {"email_account": email_account, **fields})
	settings.save(ignore_permissions=True)
	return settings.mail_accounts[0]


def make_communication(email_account, sent_or_received="Received", **fields):
	return frappe.get_doc(
		{
			"doctype": "Communication",
			"communication_type": "Communication",
			"communication_medium": "Email",
			"email_account": email_account,
			"sent_or_received": sent_or_received,
			"subject": "Offer",
			"content": "Hello",
			"sender": "client@example.test",
			"recipients": "info@acme.test",
			**fields,
		}
	).insert(ignore_permissions=True)


def eml(**headers):
	headers = {
		"From": "Client <client@example.test>",
		"To": "info@acme.test",
		"Subject": "Offer",
		"Date": "Tue, 10 Mar 2026 09:30:00 +0000",
		"Message-ID": f"<{uuid.uuid4().hex}@example.test>",
		**headers,
	}
	lines = [f"{name}: {value}" for name, value in headers.items()]
	return ("\r\n".join(lines) + "\r\n\r\nHello\r\n").encode()


EML = eml()


def restrict_to_own_company(user, own_company):
	frappe.get_doc(
		{"doctype": "User Permission", "user": user, "allow": "Own Company", "for_value": own_company}
	).insert(ignore_permissions=True)


def resume_mail_writes(case):
	from docarchive.mail import originals

	frappe.cache.delete_value(originals.PAUSE_KEY)
	case.addCleanup(frappe.cache.delete_value, originals.PAUSE_KEY)


def store_mail_inline(case):
	from docarchive.mail import originals

	resume_mail_writes(case)
	case.enterContext(patch("docarchive.mail.originals.schedule", side_effect=originals.store))
