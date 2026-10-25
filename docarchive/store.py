import base64
import hashlib
import re
from datetime import UTC, datetime

import frappe
from frappe import _

from docarchive import storage_targets

MODES = ("GOVERNANCE", "COMPLIANCE")
CLIENT_FIELDS = ("endpoint", "region", "bucket", "access_key", "secret_key")
ADMIN_FIELDS = ("admin_access_key", "admin_secret_key")
MISSING = ("404", "NoSuchKey", "NotFound")
SMOKE_PREFIX = "_smoke/"
SMOKE_KEY = re.compile(r"^_smoke/\d{8}T\d{6}Z-[0-9a-f]{8}\.txt$")
REFUSED = ("AccessDenied", "ObjectLocked")
HOLD_ON, HOLD_OFF = "ON", "OFF"


class LockError(frappe.ValidationError):
	pass


class VerifyError(frappe.ValidationError):
	pass


class Target:
	def __init__(self, name, bucket, lock_mode, client, admin_client=None):
		self.name = name
		self.bucket = bucket
		self.lock_mode = lock_mode
		self._client = client
		self._admin_client = admin_client


def _client_for(config):
	from s3storage.client import get_client

	return get_client(config={field: config[field] for field in CLIENT_FIELDS})[0]


def from_config(config) -> Target:
	admin = None
	if all(config.get(field) for field in ADMIN_FIELDS):
		admin = _client_for(
			{**config, "access_key": config.admin_access_key, "secret_key": config.admin_secret_key}
		)
	return Target(config.name, config.bucket, config.lock_mode, _client_for(config), admin)


def has_admin_access(target) -> bool:
	return target._admin_client is not None


def require_admin(target):
	if not has_admin_access(target):
		raise LockError(_("Storage target {0} has no admin key.").format(target.name))


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


def put_locked(
	target, key, body, mode, retain_until, content_type="application/octet-stream", legal_hold=False
) -> dict:
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
		**({"ObjectLockLegalHoldStatus": HOLD_ON} if legal_hold else {}),
	)
	return {"sha256": sha256, "version_id": response.get("VersionId")}


def extend_locked(target, key, version_id, sha256, mode, retain_until) -> bool:
	retain_until = validate_lock(mode, retain_until)
	found = head(target, key, version_id)
	if not found or (found.get("Metadata") or {}).get("sha256") != sha256:
		raise VerifyError(_("{0} does not match its checksum.").format(key))
	if read_sha256(target, key, version_id) != sha256:
		raise VerifyError(_("{0} does not match its checksum.").format(key))
	current = retention(target, key, version_id)
	if current["mode"] != mode:
		raise LockError(
			_("{0} is locked in mode {1}, not {2}. Run docarchive-sync-mode first.").format(
				key, current["mode"], mode
			)
		)
	if current["retain_until"] and retain_until <= current["retain_until"]:
		return False
	target._client.put_object_retention(
		Bucket=target.bucket,
		Key=key,
		VersionId=version_id,
		Retention={"Mode": mode, "RetainUntilDate": retain_until},
	)
	return True


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


def verify(target, key, version_id, sha256, mode, retain_until, legal_hold=False):
	retain_until = validate_lock(mode, retain_until)
	found = head(target, key, version_id)
	if not found:
		raise VerifyError(_("{0} is missing on the storage target.").format(key))
	if legal_hold and found.get("ObjectLockLegalHoldStatus") != HOLD_ON:
		raise VerifyError(_("The legal hold on {0} could not be verified.").format(key))
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


def hold_status(target, key, version_id) -> str:
	found = head(target, key, versioned(version_id)["VersionId"])
	if not found:
		raise VerifyError(_("{0} is missing on the storage target.").format(key))
	return found.get("ObjectLockLegalHoldStatus") or HOLD_OFF


def set_hold(target, key, version_id):
	_admin_hold(target, key, version_id, HOLD_ON)
	if hold_status(target, key, version_id) != HOLD_ON:
		raise VerifyError(_("The legal hold on {0} could not be verified.").format(key))


def release_hold(target, key, version_id):
	_admin_hold(target, key, version_id, HOLD_OFF)
	if hold_status(target, key, version_id) != HOLD_OFF:
		raise VerifyError(_("The legal hold on {0} is still set.").format(key))


def _admin_hold(target, key, version_id, status):
	require_admin(target)
	target._admin_client.put_object_legal_hold(
		Bucket=target.bucket, Key=key, **versioned(version_id), LegalHold={"Status": status}
	)


def deletable(target, key, version_id, early=False) -> dict | None:
	require_admin(target)
	found = head(target, key, versioned(version_id)["VersionId"])
	if not found:
		return None
	if found.get("ObjectLockLegalHoldStatus") == HOLD_ON:
		raise LockError(_("{0} is under legal hold.").format(key))
	until = found.get("ObjectLockRetainUntilDate")
	if early and found.get("ObjectLockMode") != "GOVERNANCE":
		raise LockError(_("{0} is not locked in GOVERNANCE mode.").format(key))
	if not early and until and until > datetime.now(UTC):
		raise LockError(_("{0} is locked until {1}.").format(key, until.isoformat()))
	return found


def delete_expired(target, key, version_id) -> bool:
	if not deletable(target, key, version_id):
		return False
	_admin_delete(target, key, version_id)
	return True


def delete_early(target, key, version_id) -> bool:
	if not deletable(target, key, version_id, early=True):
		return False
	_admin_delete(target, key, version_id, BypassGovernanceRetention=True)
	return True


def _admin_delete(target, key, version_id, **options):
	target._admin_client.delete_object(Bucket=target.bucket, Key=key, **versioned(version_id), **options)
	if head(target, key, version_id):
		raise VerifyError(_("{0} still exists after deleting it.").format(key))
