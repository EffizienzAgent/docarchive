"""Screenshots of a demo site for docs/images.

Needs Playwright (pip install playwright, then playwright install chromium) and a site filled with
bench --site <site> docarchive-demo. Settings come from the environment:

- DOCARCHIVE_BASE: site URL, default http://localhost:8000
- DOCARCHIVE_USER: default Administrator
- DOCARCHIVE_PASSWORD: required
- DOCARCHIVE_SHOTS: output folder, default docs/images

Pass screen names (e.g. thread audit-log) to take only those.
"""

import io
import os
import sys
import tempfile
import zipfile
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("DOCARCHIVE_BASE", "http://localhost:8000")
OUT = Path(os.environ.get("DOCARCHIVE_SHOTS", "docs/images"))
VIEWPORT = {"width": 1440, "height": 900}
CONTACT = "Nordlicht Bürobedarf GmbH"
HOLD_REASON = "Betriebsprüfung 2026, Anforderung vom 12.10."
DOCUMENTS = {
	"held": {"legal_hold": 1},
	"mail": {"document_type": "Correspondence", "title": ["like", "Re:%"]},
	"pending": {"document_number": "RE-2026-009"},
	"archived": {"document_number": "RE-2026-003"},
	"contract": {"document_type": "Contract"},
}


def first(page, doctype, filters, field="name"):
	rows = page.evaluate(
		"([d, f, x]) => frappe.xcall('frappe.client.get_list', {doctype: d, filters: f, fields: [x], limit_page_length: 1})",
		[doctype, filters, field],
	)
	if not rows:
		raise LookupError(f"No {doctype} matches {filters}. Is this a docarchive-demo site?")
	return rows[0][field]


def shot(page, name, wait=2500):
	page.wait_for_timeout(wait)
	page.screenshot(path=str(OUT / f"{name}.png"))
	print("saved", name)


def open_form(page, route, name):
	page.goto(f"{BASE}/app/{route}/{name}")
	page.wait_for_selector(".form-layout", timeout=20000)


def login(page):
	page.goto(f"{BASE}/login")
	page.evaluate(
		"""([usr, pwd]) => fetch('/api/method/login', {method: 'POST',
			headers: {'Content-Type': 'application/json'}, body: JSON.stringify({usr, pwd})})""",
		[os.environ.get("DOCARCHIVE_USER", "Administrator"), os.environ["DOCARCHIVE_PASSWORD"]],
	)
	page.goto(f"{BASE}/app/business-document")
	page.wait_for_selector(".frappe-list", timeout=20000)


class Screens:
	def __init__(self, browser, page):
		self.browser, self.page = browser, page

	def document(self, key):
		open_form(self.page, "business-document", first(self.page, "Business Document", DOCUMENTS[key]))

	def counterparty_panel(self):
		open_form(self.page, "contact", CONTACT)
		self.page.locator("#contact-documents_tab-tab, [data-fieldname='documents_tab'].nav-link").first.click()
		shot(self.page, "counterparty-panel", 4000)

	def document_form(self):
		self.document("contract")
		shot(self.page, "document-form")

	def document_audit_log(self):
		self.document("held")
		self.page.locator("[data-fieldname='audit_section']").scroll_into_view_if_needed()
		self.page.evaluate(
			"document.querySelector(\"[data-fieldname='audit_section'] .section-head.collapsed\")?.click()"
		)
		shot(self.page, "document-audit-log")

	def triage(self):
		self.page.goto(f"{BASE}/app/business-document")
		self.page.wait_for_selector(".frappe-list")
		self.page.evaluate("docarchive.triage_all()")
		shot(self.page, "triage", 4000)

	def archive_now(self):
		self.document("pending")
		self.page.evaluate(
			"""frappe.xcall('docarchive.archive_now.preview', {names: [cur_frm.doc.name]})
				.then((rows) => docarchive.confirm_archive(rows, () => {}))"""
		)
		shot(self.page, "archive-now")

	def legal_hold(self):
		self.document("archived")
		self.page.evaluate("docarchive.set_legal_hold([cur_frm.doc.name], () => {})")
		self.page.wait_for_timeout(1500)
		self.page.locator(".modal.show textarea").fill(HOLD_REASON)
		shot(self.page, "legal-hold")

	def retention_overview(self):
		self.page.goto(f"{BASE}/app/query-report/Retention Overview")
		shot(self.page, "retention-overview", 4000)

	def mail_viewer(self):
		self.document("mail")
		self.page.evaluate("docarchive.show_mail(cur_frm.doc.name)")
		shot(self.page, "mail-viewer", 3500)

	def thread(self):
		self.document("mail")
		self.page.evaluate("docarchive.show_thread(cur_frm.doc.name)")
		shot(self.page, "thread", 3500)

	def audit_log(self):
		self.page.goto(f"{BASE}/app/archive-audit-log")
		shot(self.page, "audit-log", 3000)

	def audit_entry(self):
		entry = first(self.page, "Archive Audit Log", {"action": "archive.run"})
		self.page.goto(f"{BASE}/app/archive-audit-log/{entry}")
		shot(self.page, "audit-entry", 3000)

	def archive_completeness(self):
		self.page.goto(f"{BASE}/app/query-report/Archive Completeness?number_gaps=1")
		shot(self.page, "archive-completeness", 4000)

	def export_viewer(self):
		export = first(self.page, "Archive Export", {"status": "Completed"})
		doc = self.page.evaluate(
			"(n) => frappe.xcall('frappe.client.get', {doctype: 'Archive Export', name: n})", export
		)
		data = self.page.request.get(f"{BASE}{doc['parts'][0]['file']}").body()
		with tempfile.TemporaryDirectory() as folder:
			zipfile.ZipFile(io.BytesIO(data)).extractall(folder)
			viewer = self.browser.new_page(viewport=VIEWPORT, device_scale_factor=2)
			viewer.goto(f"file://{folder}/index.html")
			shot(viewer, "export-viewer")
			viewer.locator("#views button").nth(1).click()
			shot(viewer, "export-viewer-audit-log", 1500)
			viewer.close()


def main(only):
	OUT.mkdir(parents=True, exist_ok=True)
	with sync_playwright() as playwright:
		browser = playwright.chromium.launch()
		page = browser.new_page(viewport=VIEWPORT, device_scale_factor=2)
		login(page)
		screens = Screens(browser, page)
		for name in [n for n in vars(Screens) if not n.startswith("_") and n not in ("document",)]:
			if not only or name.replace("_", "-") in only:
				getattr(screens, name)()
		browser.close()


if __name__ == "__main__":
	main(set(sys.argv[1:]))
