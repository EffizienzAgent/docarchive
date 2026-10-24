import csv
import json
import re
from pathlib import Path

from frappe.tests import IntegrationTestCase

APP_DIR = Path(__file__).resolve().parent.parent
PY_STRING = re.compile(r'_\(\s*"((?:[^"\\]|\\.)+)"\s*\)')
JS_STRING = re.compile(r'__\(\s*"((?:[^"\\]|\\.)+)"\s*\)')


def translations():
	with open(APP_DIR / "translations" / "de.csv", encoding="utf-8", newline="") as f:
		return {row[0]: row[1] for row in csv.reader(f) if len(row) >= 2 and row[1].strip()}


def strings():
	found = set()
	for path in APP_DIR.glob("document_archive/doctype/*/*.json"):
		doctype = json.loads(path.read_text(encoding="utf-8"))
		found.add(doctype["name"])
		for field in doctype["fields"]:
			found.update((field.get("label"), field.get("description")))
			if field["fieldtype"] == "Select":
				found.update((field.get("options") or "").split("\n"))
	for path in APP_DIR.rglob("*.py"):
		if "tests" not in path.parts and not path.name.startswith("test_"):
			found.update(PY_STRING.findall(path.read_text(encoding="utf-8")))
	for path in APP_DIR.rglob("*.js"):
		found.update(JS_STRING.findall(path.read_text(encoding="utf-8")))
	return {s for s in found if s and s.strip()}


class TestTranslations(IntegrationTestCase):
	def test_every_string_has_german_translation(self):
		known = translations()
		self.assertEqual(sorted(s for s in strings() if s not in known), [])

	def test_seeded_types_translated(self):
		known = translations()
		for name in ("Outgoing Invoice", "Incoming Invoice", "Contract", "Bank Statement"):
			self.assertIn(name, known)
