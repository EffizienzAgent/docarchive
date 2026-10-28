import re
from collections import defaultdict

import frappe
from frappe import _

from docarchive import settings

DEFAULT_KEYWORDS = (
	"Angebot",
	"Auftrag",
	"Bestellung",
	"Vertrag",
	"Rechnung",
	"Gutschrift",
	"Mahnung",
	"Kündigung",
	"Widerruf",
	"Reklamation",
	"Bescheid",
	"offer",
	"order",
	"contract",
	"invoice",
	"credit note",
	"reminder",
	"cancellation",
	"termination",
)
DEFAULT_RULE = (
	"Archive when the mail prepares, closes, performs or undoes a business deal: offer, acceptance, "
	"order, contract or contract change, binding commitment, invoice, credit note, dunning, "
	"cancellation, withdrawal, complaint with consequences. Not: notifications, newsletters, "
	"scheduling, support without contractual consequences. When in doubt, archive."
)
ATTACHMENT_KINDS = ("pdf", "xml")


def keywords() -> list[str]:
	lines = (settings.get().suggestion_keywords or "").splitlines()
	return [line.strip() for line in lines if line.strip()] or list(DEFAULT_KEYWORDS)


def reasons(subject: str | None, file_names: list[str], words: list[str]) -> list[str]:
	found = [
		f"keyword:{word}"
		for word in words
		if subject and re.search(rf"(?<!\w){re.escape(word)}", subject, re.IGNORECASE)
	]
	extensions = {name.rsplit(".", 1)[-1].lower() for name in file_names if "." in name}
	return found + [f"attachment:{kind}" for kind in ATTACHMENT_KINDS if kind in extensions]


def attachment_names(communications: list[str]) -> dict[str, list[str]]:
	names = defaultdict(list)
	if communications:
		for row in frappe.get_all(
			"File",
			filters={"attached_to_doctype": "Communication", "attached_to_name": ("in", communications)},
			fields=["attached_to_name", "file_name"],
			order_by="file_name asc",
		):
			names[row.attached_to_name].append(row.file_name)
	return {name: names[name] for name in communications}


@frappe.whitelist()
def rule() -> str:
	return settings.get().archive_rule or _(DEFAULT_RULE)
