"""Demo data for screenshots and trials. Only for development sites.

Storage is an in-memory Object Lock bucket for the lifetime of the command, so archive runs,
legal holds, extensions, audit entries and an export work without real S3 credentials.
The site keeps the database state; the in-memory objects are gone afterwards.
"""

import io
import uuid
from email.message import EmailMessage
from email.utils import format_datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import frappe
from frappe.utils import add_days, add_to_date, get_system_timezone, now_datetime

from docarchive import archive_now, audit, export, extension, legal_hold, mail, run
from docarchive.document_types import CORRESPONDENCE
from docarchive.mail import archiving, originals
from docarchive.permissions import ADMIN_ROLE, MANAGER_ROLE

DOCUMENT = run.DOCUMENT
COMPANY = "muster-handels-gmbh"
DOMAIN = "muster-handel.example"
ADMIN_KEYS = {"admin_access_key": "demo-admin", "admin_secret_key": "demo-admin"}
PARTIES = (
	"Nordlicht Bürobedarf GmbH",
	"Kranich Logistik AG",
	"Atelier Weber & Söhne",
	"Brückner IT-Service",
)
OUTGOING = (
	("RE-2026-001", "2026-01-14", 0, "Wartungsvertrag Januar", 1190.00),
	("RE-2026-002", "2026-02-03", 1, "Lagerlogistik Q1", 4760.00),
	("RE-2026-003", "2026-02-27", 2, "Messestand Gestaltung", 2380.00),
	("RE-2026-004", "2026-03-18", 0, "Wartungsvertrag März", 1190.00),
	("RE-2026-006", "2026-04-22", 3, "Netzwerkprüfung", 892.50),
	("RE-2026-007", "2026-05-12", 1, "Lagerlogistik Q2", 4760.00),
	("RE-2026-008", "2026-06-30", 2, "Katalog Satz und Druck", 3570.00),
)
INCOMING = (
	("NB-88412", "2026-01-20", 0, "Büromaterial", 214.20),
	("KL-2026-0315", "2026-03-15", 1, "Fracht März", 1428.00),
	("BIT-1107", "2026-04-02", 3, "Firewall-Lizenz", 1785.00),
)


def seed() -> dict:
	if not frappe.conf.developer_mode:
		frappe.throw("Demo data is only created on sites with developer_mode.")
	if frappe.db.count(DOCUMENT):
		frappe.throw("The site already has Business Documents. Use an empty site for demo data.")
	fakes = {}
	with (
		patch("docarchive.store._client_for", side_effect=lambda config: fake_client(fakes, config)),
		patch("frappe.enqueue"),
	):
		try:
			result = Demo().build()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title="docarchive demo")
			frappe.throw("The demo data could not be created completely. Please use a new site.")
		audit.flush()
	frappe.db.set_single_value("System Settings", "enable_scheduler", 0)
	frappe.db.commit()
	return {**result, "scheduler": "disabled, the demo storage exists only while the command runs"}


def fake_client(fakes, config):
	from docarchive.tests.fake_s3 import AdminClient, LockedFakeS3

	fake = fakes.setdefault(config["bucket"], LockedFakeS3())
	return AdminClient(fake) if config["access_key"] == "demo-admin" else fake


class Demo:
	def build(self) -> dict:
		frappe.set_user("Administrator")
		self.user_roles()
		self.company = self.own_company()
		self.settings()
		self.parties = [self.party(name) for name in PARTIES]
		self.panel_on_contacts()
		self.invoices()
		self.contract()
		broken = self.bank_statement()
		self.mail_thread()
		self.age(frappe.get_all(DOCUMENT, pluck="name"))
		frappe.db.set_value(DOCUMENT, broken, "content_sha256", "0" * 64, update_modified=False)
		frappe.db.commit()
		summary = run.nightly()
		mails = frappe.get_all(
			DOCUMENT, {"document_type": CORRESPONDENCE, "archive_status": ("!=", run.ARCHIVED)}, pluck="name"
		)
		if mails:
			archive_now.run_job(mails)
		legal_hold.set_legal_hold([by_number("RE-2026-002")], "Prüfung durch Steuerberater")
		extension.extend(by_number("NB-88412"), extension.ADD_YEARS, years=2, reason="Rechtsstreit möglich")
		self.triage()
		self.overdue()
		frappe.db.commit()
		exported = self.export()
		return {"archived": summary.get("archived"), "failed": summary.get("failed"), "export": exported}

	def user_roles(self):
		user = frappe.get_doc("User", "Administrator")
		missing = {ADMIN_ROLE, MANAGER_ROLE} - {row.role for row in user.roles}
		for role in sorted(missing):
			user.append("roles", {"role": role})
		user.time_zone = get_system_timezone()
		user.save(ignore_permissions=True)

	def own_company(self):
		if not frappe.db.exists("Own Company", {"slug": COMPANY}):
			frappe.get_doc(
				{"doctype": "Own Company", "slug": COMPANY, "company_name": "Muster Handels GmbH"}
			).insert(ignore_permissions=True)
		return frappe.db.get_value("Own Company", {"slug": COMPANY})

	def settings(self):
		settings = frappe.get_single("Archive Settings")
		settings.update({"enabled": 1, "min_age_hours": 1, "default_own_company": self.company})
		settings.set(
			"storage_targets",
			[
				{
					"target_name": name,
					"enabled": 1,
					"lock_mode": "GOVERNANCE",
					"config_source": "Fields",
					"endpoint": "https://s3.demo.example",
					"region": region,
					"bucket": f"demo-archive-{name}",
					"access_key": "demo",
					"secret_key": "demo",
					**ADMIN_KEYS,
				}
				for name, region in (("fsn1", "eu-central"), ("hel1", "eu-north"))
			],
		)
		settings.save(ignore_permissions=True)

	def party(self, name):
		existing = frappe.db.get_value("Contact", {"company_name": name})
		if existing:
			return existing
		first, _, last = name.partition(" ")
		domain = party_domain(name)
		return (
			frappe.get_doc(
				{
					"doctype": "Contact",
					"first_name": first,
					"last_name": last,
					"company_name": name,
					"email_ids": [{"email_id": f"buchhaltung@{domain}", "is_primary": 1}],
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def panel_on_contacts(self):
		for fieldname, fieldtype, label, after in (
			("documents_section", "Section Break", "Documents", "links"),
			("documents_html", "HTML", "Documents", "documents_section"),
		):
			if not frappe.db.exists("Custom Field", {"dt": "Contact", "fieldname": fieldname}):
				frappe.get_doc(
					{
						"doctype": "Custom Field",
						"dt": "Contact",
						"fieldname": fieldname,
						"fieldtype": fieldtype,
						"label": label,
						"insert_after": after,
					}
				).insert(ignore_permissions=True)
		if not frappe.db.exists("Client Script", {"dt": "Contact", "name": "Contact Documents"}):
			frappe.get_doc(
				{
					"doctype": "Client Script",
					"name": "Contact Documents",
					"dt": "Contact",
					"view": "Form",
					"enabled": 1,
					"script": 'frappe.ui.form.on("Contact", {\n\trefresh(frm) {\n\t\tdocarchive.documents_panel(frm, "documents_html", "documents_section");\n\t},\n});\n',
				}
			).insert(ignore_permissions=True)

	def document(self, document_type, number, date, party, title, amount, **fields):
		file = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"{date or 'scan'}_{number or 'ohne-nummer'}.png",
				"content": invoice_png(
					title, number, date, PARTIES[party] if party is not None else "", amount
				),
				"is_private": 1,
			}
		).insert(ignore_permissions=True)
		doc = frappe.get_doc(
			{
				"doctype": DOCUMENT,
				"document_type": document_type,
				"document_date": date,
				"document_number": number,
				"title": title,
				"file": file.file_url,
				**(
					{"counterparty_type": "Contact", "counterparty": self.parties[party]}
					if party is not None
					else {}
				),
				**fields,
			}
		).insert(ignore_permissions=True)
		return doc.name

	def invoices(self) -> list[str]:
		names = [
			self.document("Outgoing Invoice", number, date, party, title, amount)
			for number, date, party, title, amount in OUTGOING
		]
		names += [
			self.document("Incoming Invoice", number, date, party, title, amount)
			for number, date, party, title, amount in INCOMING
		]
		return names

	def contract(self) -> str:
		return self.document(
			"Contract",
			"V-2025-12",
			"2025-12-01",
			1,
			"Rahmenvertrag Lagerlogistik",
			0,
			contract_end_date="2027-11-30",
		)

	def bank_statement(self) -> str:
		return self.document("Bank Statement", "KA-2026-06", "2026-06-30", None, "Kontoauszug Juni", 0)

	def triage(self):
		for number, date, title in (
			("unbekannt", "2026-07-03", "Rechnung Papierlieferung"),
			("", "", "Scan 0042"),
		):
			self.document("Incoming Invoice", number, date or None, None, title, 98.17)

	def overdue(self):
		name = self.document(
			"Outgoing Invoice", "RE-2026-009", "2026-07-10", 3, "Netzwerk Nachprüfung", 410.55
		)
		frappe.db.set_value(DOCUMENT, name, "modified", add_days(now_datetime(), -4), update_modified=False)

	def age(self, names):
		old = add_to_date(now_datetime(), hours=-30)
		for name in names:
			frappe.db.set_value(DOCUMENT, name, "modified", old, update_modified=False)

	def mail_thread(self):
		account = frappe.db.get_value("Email Account", {"email_id": f"info@{DOMAIN}"})
		if not account:
			account = (
				frappe.get_doc(
					{
						"doctype": "Email Account",
						"email_account_name": "Muster Info",
						"email_id": f"info@{DOMAIN}",
						"enable_incoming": 0,
						"enable_outgoing": 0,
					}
				)
				.insert(ignore_permissions=True)
				.name
			)
		settings = frappe.get_single("Archive Settings")
		settings.set(
			"mail_accounts", [{"email_account": account, "mode": "Manual", "internal_domains": DOMAIN}]
		)
		settings.save(ignore_permissions=True)
		party = PARTIES[1]
		sender = f"dispo@{party_domain(party)}"
		first, middle, last = (f"<{uuid.uuid4().hex}@demo.example>" for _ in range(3))
		thread = (
			(
				"2026-06-02 08:41:00",
				first,
				None,
				"Anfrage Zusatzlager Juli",
				sender,
				"Received",
				"Guten Tag,\n\nkönnen Sie uns für Juli zusätzlich 40 Palettenplätze anbieten?\n\nViele Grüße\nKranich Logistik",
			),
			(
				"2026-06-04 15:12:00",
				last,
				f"{first} {middle}",
				"Re: Anfrage Zusatzlager Juli",
				sender,
				"Received",
				"Danke für das Angebot. Wir nehmen die 40 Plätze ab dem 1. Juli.\n\nViele Grüße\nKranich Logistik",
			),
		)
		for at, message_id, references, subject, from_address, direction, body in thread:
			comm = frappe.get_doc(
				{
					"doctype": "Communication",
					"communication_type": "Communication",
					"communication_medium": "Email",
					"email_account": account,
					"sent_or_received": direction,
					"subject": subject,
					"content": body,
					"sender": from_address,
					"recipients": f"info@{DOMAIN}",
					"communication_date": at,
				}
			).insert(ignore_permissions=True)
			msg = EmailMessage()
			msg["From"] = f"{party} <{from_address}>"
			msg["To"] = f"Muster Handels GmbH <info@{DOMAIN}>"
			msg["Subject"] = subject
			msg["Date"] = format_datetime(
				frappe.utils.get_datetime(at).replace(tzinfo=ZoneInfo(get_system_timezone()))
			)
			msg["Message-ID"] = message_id
			if references:
				msg["In-Reply-To"] = references.split()[-1]
				msg["References"] = references
			msg.set_content(body)
			original = mail.capture(comm, msg.as_bytes(), "demo")
			if not original:
				frappe.throw(
					"The demo mail could not be captured. See the Error Log (docarchive mail capture)."
				)
			originals.store(original)
			name = archiving.archive(original)
			doc = frappe.get_doc(DOCUMENT, name)
			if not doc.counterparty:
				doc.update({"counterparty_type": "Contact", "counterparty": self.parties[1]})
				doc.save(ignore_permissions=True)

	def export(self):
		return export.run_export(export.start_export({"own_company": self.company}))


def by_number(number) -> str:
	return frappe.db.get_value(DOCUMENT, {"document_number": number})


def party_domain(name) -> str:
	return "".join(char for char in name.lower() if char.isascii() and char.isalnum())[:16] + ".example"


def invoice_png(title, number, date, party, amount) -> bytes:
	"""A page-sized PNG with readable invoice text, so previews work in every browser."""
	from PIL import Image, ImageDraw, ImageFont

	page = Image.new("RGB", (1240, 1754), "white")
	draw = ImageDraw.Draw(page)

	def font(size):
		try:
			return ImageFont.truetype("DejaVuSans.ttf", size)
		except OSError:
			return ImageFont.load_default(size=size)

	def text(x, y, size, value, fill="#1f2328"):
		draw.text((x, y), value, font=font(size), fill=fill)

	text(110, 120, 44, "Muster Handels GmbH")
	text(110, 180, 22, "Hafenstraße 12 · 20457 Hamburg", "#59636e")
	draw.line((110, 230, 1130, 230), fill="#d1d9e0", width=2)
	text(110, 300, 26, party)
	text(110, 440, 36, title)
	text(110, 500, 24, f"Nummer: {number}")
	text(110, 540, 24, f"Datum: {date}")
	if amount:
		net = amount / 1.19
		rows = (
			("Leistung", "Betrag", "#59636e"),
			(title, f"{net:,.2f} EUR", None),
			("zzgl. 19 % USt.", f"{amount - net:,.2f} EUR", None),
		)
		for index, (label, value, fill) in enumerate(rows):
			text(110, 660 + index * 50, 24, label, fill or "#1f2328")
			text(860, 660 + index * 50, 24, value, fill or "#1f2328")
		draw.line((110, 820, 1130, 820), fill="#1f2328", width=2)
		text(110, 840, 28, "Gesamt")
		text(860, 840, 28, f"{amount:,.2f} EUR")
	out = io.BytesIO()
	page.save(out, "PNG", optimize=True)
	return out.getvalue()
