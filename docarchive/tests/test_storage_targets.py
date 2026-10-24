from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from docarchive import storage_targets
from docarchive.tests.factories import fields_target, make_settings, site_config_target

SITE_CONFIG = {
	"archive_primary_endpoint": "https://s3.region-a.example.test",
	"archive_primary_region": "region-a",
	"archive_primary_bucket": "archive-a",
	"archive_secondary_endpoint": "https://s3.region-b.example.test",
	"archive_secondary_region": "region-b",
	"archive_secondary_bucket": "archive-b",
	"archive_access_key": "key",
	"archive_secret_key": "secret",
}


class TestStorageTargets(IntegrationTestCase):
	def config(self, values=SITE_CONFIG):
		return patch.dict(frappe.local.conf, values)

	def test_no_targets_while_disabled(self):
		make_settings(targets=[])
		self.assertEqual(storage_targets.resolve(), [])
		self.assertIsNone(storage_targets.configured())

	def test_enabling_needs_an_enabled_target(self):
		for targets in ([], [site_config_target(enabled=0)]):
			with self.subTest(targets=targets), self.config(), self.assertRaises(frappe.ValidationError):
				make_settings(enabled=1, targets=targets)

	def test_site_config_targets_resolve(self):
		with self.config():
			make_settings(enabled=1, targets=[site_config_target("primary"), site_config_target("secondary")])
			targets = storage_targets.configured()
		self.assertEqual([t.name for t in targets], ["primary", "secondary"])
		self.assertEqual(
			targets[1],
			{
				"name": "secondary",
				"lock_mode": "GOVERNANCE",
				"endpoint": "https://s3.region-b.example.test",
				"region": "region-b",
				"bucket": "archive-b",
				"access_key": "key",
				"secret_key": "secret",
			},
		)

	def test_enabling_with_missing_site_config_rejected(self):
		partial = {k: v for k, v in SITE_CONFIG.items() if k != "archive_secondary_bucket"}
		with patch.dict(frappe.local.conf, partial, clear=True):
			with self.assertRaisesRegex(frappe.ValidationError, "archive_secondary_bucket"):
				make_settings(
					enabled=1, targets=[site_config_target("primary"), site_config_target("secondary")]
				)
			make_settings(targets=[site_config_target("primary"), site_config_target("secondary")])
			self.assertIsNone(storage_targets.configured())

	def test_field_target_keeps_secret_encrypted(self):
		make_settings(targets=[fields_target("direct", secret_key="synthetic-secret")])
		row = frappe.get_single("Archive Settings").storage_targets[0]
		self.assertNotEqual(row.secret_key, "synthetic-secret")
		stored = frappe.db.get_value("Archive Storage Target", row.name, "secret_key")
		self.assertNotIn("synthetic-secret", str(stored))
		target = storage_targets.resolve()[0]
		self.assertEqual((target.access_key, target.secret_key), ("key", "synthetic-secret"))

	def test_lock_mode_per_target(self):
		with self.config():
			make_settings(
				targets=[
					site_config_target("primary"),
					site_config_target("secondary", lock_mode="COMPLIANCE"),
				]
			)
			self.assertEqual([t.lock_mode for t in storage_targets.resolve()], ["GOVERNANCE", "COMPLIANCE"])
		with self.assertRaises(frappe.ValidationError):
			make_settings(targets=[site_config_target(lock_mode="NONE")])

	def test_disabled_target_is_skipped(self):
		with self.config():
			make_settings(targets=[site_config_target("primary"), site_config_target("secondary", enabled=0)])
			self.assertEqual([t.name for t in storage_targets.resolve()], ["primary"])

	def test_invalid_targets_rejected(self):
		cases = {
			"duplicate name": [
				site_config_target("primary"),
				site_config_target("primary", config_prefix="other"),
			],
			"same bucket": [
				site_config_target("primary"),
				site_config_target("copy", config_prefix="archive_primary"),
			],
			"bad name": [site_config_target("Primary Target")],
			"bad prefix": [site_config_target(config_prefix="archive-primary")],
			"secret in key name": [site_config_target(secret_key_config_key="AKIA/with+secret")],
			"missing key name": [site_config_target(access_key_config_key=None)],
			"plain http": [fields_target(endpoint="http://s3.example.test")],
			"bad bucket": [fields_target(bucket="Archive_A")],
			"no region": [fields_target(region=None)],
			"no secret": [fields_target(secret_key=None)],
		}
		for case, targets in cases.items():
			with self.subTest(case), self.assertRaises(frappe.ValidationError):
				make_settings(targets=targets)

	def test_site_config_values_validated_once_set(self):
		cases = {
			"plain http": {"archive_primary_endpoint": "http://s3.region-a.example.test"},
			"bad bucket": {"archive_primary_bucket": "Archive_A"},
		}
		for case, values in cases.items():
			with self.subTest(case), self.config({**SITE_CONFIG, **values}):
				with self.assertRaises(frappe.ValidationError):
					make_settings(targets=[site_config_target("primary")])

	def test_same_bucket_across_target_kinds_rejected(self):
		direct = fields_target("direct", endpoint="https://s3.region-a.example.test/", bucket="archive-a")
		with self.config(), self.assertRaises(frappe.ValidationError):
			make_settings(targets=[site_config_target("primary"), direct])
		with self.config():
			make_settings(targets=[site_config_target("primary"), fields_target("direct")])

	def test_internal_endpoints_rejected(self):
		for endpoint in (
			"https://10.0.0.5",
			"https://192.168.1.10:9000",
			"https://127.0.0.1",
			"https://169.254.169.254",
			"https://[::1]",
			"https://[fd00::1]",
			"https://localhost:9000",
			"https://minio.internal",
			"https://nas.local",
			"https://minio",
		):
			with self.subTest(endpoint=endpoint), self.assertRaises(frappe.ValidationError):
				make_settings(targets=[fields_target(endpoint=endpoint)])
		with self.config({**SITE_CONFIG, "archive_primary_endpoint": "https://10.1.2.3"}):
			with self.assertRaises(frappe.ValidationError):
				make_settings(targets=[site_config_target("primary")])
		make_settings(targets=[fields_target(endpoint="https://s3.eu-central-1.example.test")])
