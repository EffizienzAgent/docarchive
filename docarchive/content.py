import hashlib
import os

import frappe

FOLDERS = {"/private/files/": ("private", "files"), "/files/": ("public", "files")}


def read_bytes(file_url) -> bytes | None:
	for method in reversed(frappe.get_hooks("docarchive_read_file")):
		data = frappe.get_attr(method)(file_url)
		if data is not None:
			return data
	return read_local(file_url)


def read_local(file_url) -> bytes | None:
	for prefix, folder in FOLDERS.items():
		if not (file_url or "").startswith(prefix):
			continue
		base = os.path.realpath(frappe.get_site_path(*folder))
		path = os.path.realpath(os.path.join(base, file_url[len(prefix) :]))
		if path.startswith(base + os.sep) and os.path.isfile(path):
			with open(path, "rb") as f:
				return f.read()
	return None


def sha256(data) -> str:
	return hashlib.sha256(data).hexdigest()
