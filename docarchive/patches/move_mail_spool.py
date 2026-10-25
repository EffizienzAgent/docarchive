import os
import shutil

import frappe

from docarchive.mail import originals

SPOOL = ("private", "docarchive", "mail-spool")
LOST = (
	"The spool file of v0.8.0 is not readable on this server, so the raw bytes of this mail were not kept. "
	"Hand the mail in again (e.g. read it again with mailsync) to retry."
)


def execute():
	folder = frappe.get_site_path(*SPOOL)
	names = frappe.get_all(
		originals.ORIGINAL,
		filters={"status": originals.PENDING, "email_queue": ("is", "not set"), "file": ("is", "not set")},
		pluck="name",
	)
	for name in names:
		raw = _read(os.path.join(folder, f"{name}.eml"))
		if raw is None:
			frappe.db.set_value(
				originals.ORIGINAL, name, {"status": originals.FAILED, "error": LOST}, update_modified=False
			)
			continue
		original = frappe.get_doc(originals.ORIGINAL, name)
		original.db_set("file", originals.store_file(original, raw), update_modified=False)
		originals.schedule(name)
	frappe.db.after_commit.add(lambda: shutil.rmtree(folder, ignore_errors=True))


def _read(path):
	try:
		with open(path, "rb") as f:
			return f.read()
	except OSError:
		return None
