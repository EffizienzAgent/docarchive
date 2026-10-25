import os

import frappe
from frappe.utils import strip_html

from docarchive.mail import originals

SPOOL = ("private", "docarchive", "mail-spool")
SAVEPOINT = "docarchive_move_mail_spool"
LOST = (
	"The spool file of v0.8.0 is not readable on this server, so the raw bytes of this mail were not kept. "
	"Hand the mail in again (e.g. read it again with mailsync) to retry."
)


def execute():
	folder = frappe.get_site_path(*SPOOL)
	moved = []
	for row in frappe.get_all(
		originals.ORIGINAL,
		filters={
			"status": ("in", (originals.PENDING, originals.FAILED)),
			"email_queue": ("is", "not set"),
			"file": ("is", "not set"),
			"raw_fallback": ("is", "not set"),
		},
		fields=["name", "status"],
	):
		path = os.path.join(folder, f"{row.name}.eml")
		raw = _read(path)
		if raw is None:
			if row.status == originals.PENDING:
				_mark_failed(row.name, LOST)
			continue
		try:
			frappe.db.savepoint(SAVEPOINT)
			if _move(row.name, raw):
				moved.append(path)
		except Exception as error:
			frappe.db.rollback(save_point=SAVEPOINT)
			frappe.log_error(title=f"docarchive mail spool: {row.name}")
			_mark_failed(row.name, f"Moving the spool file failed: {error}")
	frappe.db.after_commit.add(lambda: _remove(moved, folder))


def _move(name, raw):
	if not originals._write(frappe.get_doc(originals.ORIGINAL, name), raw):
		return False
	frappe.db.set_value(
		originals.ORIGINAL,
		name,
		{"status": originals.PENDING, "attempts": 0, "error": None},
		update_modified=False,
	)
	originals.schedule(name)
	return True


def _mark_failed(name, message):
	frappe.db.set_value(
		originals.ORIGINAL,
		name,
		{"status": originals.FAILED, "error": strip_html(message)[: originals.ERROR_LENGTH]},
		update_modified=False,
	)


def _read(path):
	try:
		with open(path, "rb") as f:
			return f.read()
	except OSError:
		return None


def _remove(paths, folder):
	for path in paths:
		try:
			os.remove(path)
		except OSError:
			pass
	try:
		os.rmdir(folder)
	except OSError:
		pass
