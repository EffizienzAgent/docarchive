import base64
import json

import frappe
from frappe.tests import IntegrationTestCase

from docarchive import export_package
from docarchive.tests.factories import make_file, make_settings

PNG = base64.b64decode(
	"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
NEUTRAL = {"export_brand_name": None, "export_brand_logo": None, "export_brand_color": None, "export_show_credit": 1}


def payload(html) -> dict:
	return json.loads(html.split("const DATA = ", 1)[1].split(";\n", 1)[0])


class TestExportBranding(IntegrationTestCase):
	def tearDown(self):
		make_settings(**NEUTRAL)

	def logo(self, content=None, name="logo.png"):
		content = content or PNG + frappe.generate_hash().encode()
		return make_file(content=content, file_name=name, is_private=0).file_url

	def test_neutral_without_configuration(self):
		make_settings(**NEUTRAL)
		self.assertEqual(export_package.brand(), {"name": None, "logo": None, "color": None, "credit": True})
		make_settings(**{**NEUTRAL, "export_show_credit": 0})
		self.assertFalse(export_package.brand()["credit"])

	def test_color_must_be_hex(self):
		for color in ("#1a7f37", "#FFF", " #abcdef "):
			make_settings(**{**NEUTRAL, "export_brand_color": color})
		self.assertEqual(export_package.brand()["color"], "#abcdef")
		for color in ("red", "#12345", "#1a7f37;background:url(x)", "javascript:1"):
			with self.assertRaises(frappe.ValidationError, msg=color):
				make_settings(**{**NEUTRAL, "export_brand_color": color})

	def test_logo_must_be_a_small_raster_image(self):
		svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
		cases = [
			(svg, "logo.svg"),
			(svg, "fake.png"),
			(PNG + b"jpg", "logo.jpg"),
			(PNG + b"\0" * export_package.LOGO_LIMIT, "big.png"),
		]
		for content, name in cases:
			with self.assertRaises(frappe.ValidationError, msg=name):
				make_settings(**{**NEUTRAL, "export_brand_logo": self.logo(content, name)})
		make_settings(**{**NEUTRAL, "export_brand_logo": self.logo()})

	def test_logo_is_embedded_as_data_uri(self):
		content = PNG + b"embedded"
		make_settings(**{**NEUTRAL, "export_brand_name": "Muster GmbH", "export_brand_logo": self.logo(content)})
		brand = export_package.brand()
		self.assertEqual(brand["logo"], "data:image/png;base64," + base64.b64encode(content).decode())
		html = export_package.viewer({"scope": {}, "documents": [], "mismatches": []}, brand)
		self.assertEqual(payload(html)["brand"]["logo"], brand["logo"])
		self.assertNotIn("/files/logo", html)

	def test_changed_logo_file_is_dropped_at_export(self):
		url = self.logo()
		make_settings(**{**NEUTRAL, "export_brand_logo": url})
		with open(frappe.get_site_path("public", url.lstrip("/")), "wb") as f:
			f.write(b"<svg/>")
		self.assertIsNone(export_package.brand()["logo"])

	def test_brand_values_cannot_break_out_of_the_script(self):
		name = "</script><script>alert(1)</script> &  "
		html = export_package.viewer(
			{"scope": {}, "documents": [], "mismatches": []}, {"name": name, "color": None, "logo": None, "credit": True}
		)
		data = html.split("const DATA = ", 1)[1].split(";\n", 1)[0]
		for char in ("<", ">", "&", " "):
			self.assertNotIn(char, data)
		self.assertEqual(payload(html)["brand"]["name"], name)
		self.assertNotIn(export_package.CORE_MARKER, html)
		self.assertNotIn(export_package.DATA_MARKER, html)
		self.assertIn("const ViewerCore", html)
		self.assertNotIn("</script", export_package.VIEWER_CORE.read_text())
