import re

import frappe
from frappe import _
from frappe.utils.password import get_decrypted_password

from docarchive.slug import validate_slug

SITE_CONFIG = "Site Config"
FIELDS = "Fields"
LOCATION = ("endpoint", "region", "bucket")
CREDENTIALS = ("access_key", "secret_key")
CONFIG_KEY = re.compile(r"^[a-z][a-z0-9_]*$")
BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")


def validate(settings):
	names, places = set(), set()
	for row in settings.storage_targets:
		validate_slug(row.target_name, "Target Name")
		if row.target_name in names:
			frappe.throw(_("There is more than one storage target named {0}.").format(row.target_name))
		names.add(row.target_name)
		place = site_config_place(row) if row.config_source == SITE_CONFIG else fields_place(row)
		if place in places:
			frappe.throw(
				_("Storage target {0} points to the same bucket as another target.").format(row.target_name)
			)
		places.add(place)
	if settings.enabled:
		validate_enabled(settings)


def site_config_place(row):
	for label, value in (
		("Site Config Prefix", row.config_prefix),
		("Access Key Config Key", row.access_key_config_key),
		("Secret Key Config Key", row.secret_key_config_key),
	):
		if not CONFIG_KEY.match(value or ""):
			frappe.throw(
				_("{0} of storage target {1} must be a site config key name.").format(
					_(label), row.target_name
				)
			)
	return (SITE_CONFIG, row.config_prefix)


def fields_place(row):
	if not (row.endpoint or "").startswith("https://"):
		frappe.throw(
			_("The endpoint of storage target {0} must start with https://.").format(row.target_name)
		)
	if not BUCKET.match(row.bucket or ""):
		frappe.throw(_("Storage target {0} has no valid bucket name.").format(row.target_name))
	if not row.region:
		frappe.throw(_("Storage target {0} needs a region.").format(row.target_name))
	if not (row.access_key and row.secret_key):
		frappe.throw(_("Storage target {0} needs an access key and a secret key.").format(row.target_name))
	return (FIELDS, row.endpoint.rstrip("/"), row.bucket)


def validate_enabled(settings):
	targets = [row for row in settings.storage_targets if row.enabled]
	if not targets:
		frappe.throw(_("Enable at least one storage target before enabling the archive."))
	for row in targets:
		missing = missing_config(row)
		if missing:
			frappe.throw(
				_("Storage target {0} misses these site config keys: {1}").format(
					row.target_name, ", ".join(missing)
				)
			)


def config_keys(row) -> list[str]:
	return [
		*(f"{row.config_prefix}_{name}" for name in LOCATION),
		row.access_key_config_key,
		row.secret_key_config_key,
	]


def missing_config(row) -> list[str]:
	if row.config_source != SITE_CONFIG:
		return []
	return [key for key in config_keys(row) if not frappe.conf.get(key)]


def resolve(settings=None) -> list[frappe._dict]:
	settings = settings or frappe.get_cached_doc("Archive Settings")
	return [target(row) for row in settings.storage_targets if row.enabled]


def target(row) -> frappe._dict:
	if row.config_source == SITE_CONFIG:
		location = {name: frappe.conf.get(f"{row.config_prefix}_{name}") for name in LOCATION}
		credentials = {
			"access_key": frappe.conf.get(row.access_key_config_key),
			"secret_key": frappe.conf.get(row.secret_key_config_key),
		}
	else:
		location = {name: row.get(name) for name in LOCATION}
		credentials = {
			name: get_decrypted_password(row.doctype, row.name, name, raise_exception=False)
			for name in CREDENTIALS
		}
	return frappe._dict(name=row.target_name, lock_mode=row.lock_mode, **location, **credentials)


def configured() -> list[frappe._dict] | None:
	targets = resolve()
	complete = all(t.get(name) for t in targets for name in (*LOCATION, *CREDENTIALS))
	return targets if targets and complete else None
