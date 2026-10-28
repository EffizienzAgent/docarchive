import subprocess
import sys

import frappe
from frappe.tests import IntegrationTestCase

from docarchive import mail

EXPORTS = {
	"archive_originals": "inbox",
	"candidates": "inbox",
	"count_candidates": "inbox",
	"ignore": "inbox",
	"unignore": "inbox",
	"capture": "originals",
	"thread_pending": "thread",
}

SCRIPT = """
import sys, threading
import frappe
errors = []

def load(name):
	try:
		__import__(name)
	except Exception as e:
		errors.append(f"{name}: {e!r}")

names = ["suggestion", "archiving", "inbox", "thread"]
threads = [threading.Thread(target=load, args=(f"docarchive.mail.{n}",)) for n in names]
[t.start() for t in threads]
[t.join() for t in threads]
print("\\n".join(errors))
"""


class TestMailImports(IntegrationTestCase):
	def test_concurrent_cold_imports_do_not_deadlock(self):
		sites_dir = frappe.utils.get_bench_path() + "/sites"
		for _ in range(20):
			result = subprocess.run(
				[sys.executable, "-c", SCRIPT],
				cwd=sites_dir,
				capture_output=True,
				text=True,
				timeout=60,
			)
			self.assertEqual(result.stdout.strip(), "", result.stderr)

	def test_package_exports_resolve(self):
		for name, module in EXPORTS.items():
			function = frappe.get_attr(f"docarchive.mail.{name}")
			submodule = __import__(f"docarchive.mail.{module}", fromlist=[name])
			self.assertIs(getattr(mail, name), getattr(submodule, name))
			self.assertIs(function, getattr(submodule, name))

	def test_unknown_attribute_raises(self):
		with self.assertRaises(AttributeError):
			mail.does_not_exist
