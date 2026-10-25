import datetime

from frappe.tests import UnitTestCase

from docarchive.file_names import date_from_file_name

TODAY = datetime.date(2026, 10, 25)


def parse(name):
	date, title = date_from_file_name(name, TODAY)
	return (date.isoformat() if date else None), title


class TestFileNames(UnitTestCase):
	def test_iso_date_is_stripped_from_title(self):
		self.assertEqual(parse("2026-08-05 Rechnung Acme.pdf"), ("2026-08-05", "Rechnung Acme"))

	def test_other_formats(self):
		self.assertEqual(parse("Rechnung_2026_08_05_Acme.pdf"), ("2026-08-05", "Rechnung Acme"))
		self.assertEqual(parse("20260805-Vertrag.pdf"), ("2026-08-05", "Vertrag"))
		self.assertEqual(parse("Mahnung 05.08.2026.pdf"), ("2026-08-05", "Mahnung"))

	def test_first_valid_date_wins(self):
		self.assertEqual(parse("2026-01-02 bis 2026-03-04.pdf")[0], "2026-01-02")
		self.assertEqual(parse("2026-13-40 und 2026-03-04.pdf")[0], "2026-03-04")

	def test_invalid_or_out_of_range(self):
		for name in (
			"20260231.pdf",
			"19891231.pdf",
			"20281231.pdf",
			"Order 1234567890.pdf",
			"x2026-08-051.pdf",
			"2026-08_05.pdf",
		):
			self.assertIsNone(parse(name)[0], name)
		self.assertEqual(parse("20271231.pdf")[0], "2027-12-31")

	def test_without_date(self):
		self.assertEqual(parse("Contract_acme-2026.pdf"), (None, "Contract acme 2026"))

	def test_only_a_date(self):
		self.assertEqual(parse("2026-08-05.pdf"), ("2026-08-05", "2026 08 05"))
