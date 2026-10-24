import base64
import hashlib
import re
from datetime import UTC, datetime

import frappe
from frappe import _

from docarchive import storage_targets

MODES = ("GOVERNANCE", "COMPLIANCE")
CLIENT_FIELDS = ("endpoint", "region", "bucket", "access_key", "secret_key")
MISSING = ("404", "NoSuchKey", "NotFound")
SMOKE_PREFIX = "_smoke/"
SMOKE_KEY = re.compile(r"^_smoke/\d{8}T\d{6}Z-[0-9a-f]{8}\.txt$")
REFUSED = ("AccessDenied", "ObjectLocked")


class LockError(frappe.ValidationError):
	pass


class VerifyError(frappe.ValidationError):
	pass


class Target:
	def __init__(self, name, bucket, lock_mode, client):
		self.name = name
		self.bucket = bucket
		self.lock_mode = lock_mode
		self._client = client


def _client_for(config):
	from s3storage.client import get_client

	return get_client(config={field: config[field] for field in CLIENT_FIELDS})[0]


def from_config(config) -> Target:
	return Target(config.name, config.bucket, config.lock_mode, _client_for(config))


def targets() -> list[Target] | None:
	configured = storage_targets.configured()
	return [from_config(config) for config in configured] if configured else None


def error_code(error):
	response = getattr(error, "response", None)
	return (response or {}).get("Error", {}).get("Code") if isinstance(response, dict) else None


def refused(error) -> bool:
	code = error_code(error)
	if code in REFUSED:
		return True
	message = str(getattr(error, "response", {}).get("Error", {}).get("Message", "")).lower()
	return code == "InvalidRequest" and ("worm" in message or "lock" in message)


def validate_lock(mode, retain_until) -> datetime:
	if mode not in MODES:
		raise LockError(_("Invalid lock mode {0}.").format(mode))
	if isinstance(retain_until, str):
		try:
			retain_until = datetime.fromisoformat(retain_until)
		except ValueError:
			raise LockError(_("Invalid retain-until date {0}.").format(retain_until))
	if not isinstance(retain_until, datetime) or retain_until.utcoffset() is None:
		raise LockError(_("The retain-until date must be a date and time with a time zone."))
	if retain_until <= datetime.now(UTC):
		raise LockError(_("The retain-until date must be in the future."))
	return retain_until.astimezone(UTC)


def put_locked(target, key, body, mode, retain_until, content_type="application/octet-stream") -> dict:
	retain_until = validate_lock(mode, retain_until)
	if not key or not isinstance(body, bytes):
		raise LockError(_("An archive object needs a key and bytes."))
	sha256 = hashlib.sha256(body).hexdigest()
	response = target._client.put_object(
		Bucket=target.bucket,
		Key=key,
		Body=body,
		ContentType=content_type,
		ContentMD5=base64.b64encode(hashlib.md5(body, usedforsecurity=False).digest()).decode(),
		Metadata={"sha256": sha256},
		ObjectLockMode=mode,
		ObjectLockRetainUntilDate=retain_until,
	)
	return {"sha256": sha256, "version_id": response.get("VersionId")}


def versioned(version_id):
	if not version_id:
		raise LockError(_("An archive object version is missing. The bucket needs Object Lock."))
	return {"VersionId": version_id}


def head(target, key, version_id=None) -> dict | None:
	kwargs = versioned(version_id) if version_id else {}
	try:
		return target._client.head_object(Bucket=target.bucket, Key=key, **kwargs)
	except Exception as e:
		if error_code(e) in MISSING:
			return None
		raise


def read_sha256(target, key, version_id) -> str:
	response = target._client.get_object(Bucket=target.bucket, Key=key, **versioned(version_id))
	digest = hashlib.sha256()
	for chunk in iter(lambda: response["Body"].read(1024 * 1024), b""):
		digest.update(chunk)
	return digest.hexdigest()


def retention(target, key, version_id) -> dict:
	response = target._client.get_object_retention(Bucket=target.bucket, Key=key, **versioned(version_id))
	value = response.get("Retention") or {}
	return {"mode": value.get("Mode"), "retain_until": value.get("RetainUntilDate")}


def verify(target, key, version_id, sha256, mode, retain_until):
	retain_until = validate_lock(mode, retain_until)
	found = head(target, key, version_id)
	if not found:
		raise VerifyError(_("{0} is missing on the storage target.").format(key))
	until = found.get("ObjectLockRetainUntilDate")
	if found.get("ObjectLockMode") != mode or not until or until < retain_until:
		raise VerifyError(_("{0} is not locked as expected.").format(key))
	if (found.get("Metadata") or {}).get("sha256") != sha256 or read_sha256(
		target, key, version_id
	) != sha256:
		raise VerifyError(_("{0} does not match its checksum.").format(key))


def smoke_delete(target, key, version_id, bypass=False):
	if not SMOKE_KEY.match(key or "") or not version_id:
		raise LockError(_("Only smoke test objects may be deleted."))
	kwargs = {"BypassGovernanceRetention": True} if bypass else {}
	target._client.delete_object(Bucket=target.bucket, Key=key, VersionId=version_id, **kwargs)
