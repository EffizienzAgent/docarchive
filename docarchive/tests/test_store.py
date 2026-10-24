import ast
import base64
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from frappe.tests import IntegrationTestCase

from docarchive import store
from docarchive.tests.fake_s3 import LockedFakeS3

APP_DIR = Path(store.__file__).resolve().parent
CLIENT_CALLS = {
	"put_object": "put_locked",
	"head_object": "head",
	"get_object": "read_sha256",
	"get_object_retention": "retention",
	"delete_object": "smoke_delete",
	"BypassGovernanceRetention": "smoke_delete",
}
BANNED = {
	"put_object_retention",
	"delete_objects",
	"copy_object",
	"upload_file",
	"upload_fileobj",
	"download_file",
	"download_fileobj",
	"create_multipart_upload",
	"upload_part",
	"upload_part_copy",
	"complete_multipart_upload",
	"generate_presigned_url",
	"generate_presigned_post",
	"put_object_legal_hold",
	"put_object_lock_configuration",
	"put_bucket_policy",
	"put_bucket_versioning",
	"put_bucket_lifecycle_configuration",
	"delete_bucket",
}
CLIENT_MODULES = ("boto3", "botocore", "s3storage.client")


def future(days=1):
	return datetime.now(UTC) + timedelta(days=days)


def fake_target(name="primary", fake=None, lock_mode="GOVERNANCE"):
	return store.Target(name, f"archive-{name}", lock_mode, fake or LockedFakeS3())


def app_modules():
	for path in APP_DIR.rglob("*.py"):
		relative = path.relative_to(APP_DIR)
		if "tests" not in relative.parts:
			yield relative.as_posix(), ast.parse(path.read_text(encoding="utf-8"))


def names_in(node):
	if isinstance(node, ast.Attribute):
		yield node.attr
	elif isinstance(node, ast.Constant) and isinstance(node.value, str):
		yield node.value
	elif isinstance(node, ast.keyword) and node.arg:
		yield node.arg
	elif isinstance(node, ast.Import):
		yield from (f"import:{alias.name}" for alias in node.names)
	elif isinstance(node, ast.ImportFrom) and node.module:
		yield f"import:{node.module}"
		yield from (f"import:{node.module}.{alias.name}" for alias in node.names)


def violations(modules):
	found = []
	for path, tree in modules:
		functions = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
		for node in ast.walk(tree):
			line = getattr(node, "lineno", 0)
			owner = next((f.name for f in functions if f.lineno <= line <= (f.end_lineno or f.lineno)), None)
			for name in names_in(node):
				module = name.removeprefix("import:")
				if name in BANNED:
					found.append((path, owner, name))
				elif name in CLIENT_CALLS and (path, owner) != ("store.py", CLIENT_CALLS[name]):
					found.append((path, owner, name))
				elif name == "_client" and path != "store.py":
					found.append((path, owner, name))
				elif name.startswith("import:") and module.startswith(CLIENT_MODULES) and path != "store.py":
					found.append((path, owner, name))
	return found


class TestStore(IntegrationTestCase):
	def test_put_locked_sends_lock_headers_md5_and_sha256(self):
		target = fake_target()
		until = future(30)
		result = store.put_locked(target, "a/b.pdf", b"%PDF-data", "GOVERNANCE", until, "application/pdf")
		[put] = target._client.puts()
		self.assertEqual(put["ObjectLockMode"], "GOVERNANCE")
		self.assertEqual(put["ObjectLockRetainUntilDate"], until)
		self.assertEqual(put["ContentMD5"], base64.b64encode(hashlib.md5(b"%PDF-data").digest()).decode())
		self.assertEqual(put["Metadata"], {"sha256": hashlib.sha256(b"%PDF-data").hexdigest()})
		self.assertEqual(
			(put["Bucket"], put["Key"], put["ContentType"]), ("archive-primary", "a/b.pdf", "application/pdf")
		)
		self.assertEqual(result["sha256"], put["Metadata"]["sha256"])
		self.assertTrue(result["version_id"])

	def test_compliance_mode_accepted(self):
		target = fake_target()
		store.put_locked(target, "k", b"x", "COMPLIANCE", future())
		self.assertEqual(target._client.puts()[0]["ObjectLockMode"], "COMPLIANCE")

	def test_retain_until_converted_to_utc(self):
		target = fake_target()
		local = (datetime.now(UTC) + timedelta(days=2)).astimezone(ZoneInfo("Europe/Berlin"))
		store.put_locked(target, "k", b"x", "GOVERNANCE", local.isoformat())
		sent = target._client.puts()[0]["ObjectLockRetainUntilDate"]
		self.assertEqual((sent, sent.utcoffset()), (local, timedelta(0)))

	def test_invalid_lock_makes_no_call(self):
		naive = datetime.now() + timedelta(days=1)
		cases = {
			"missing date": ("GOVERNANCE", None),
			"past date": ("GOVERNANCE", datetime.now(UTC) - timedelta(seconds=1)),
			"now": ("GOVERNANCE", datetime.now(UTC)),
			"naive date": ("GOVERNANCE", naive),
			"naive string": ("GOVERNANCE", naive.isoformat()),
			"unparseable": ("GOVERNANCE", "next year"),
			"date only": ("GOVERNANCE", future().date()),
			"no mode": (None, future()),
			"lowercase mode": ("governance", future()),
			"unknown mode": ("LEGAL_HOLD", future()),
		}
		for case, (mode, until) in cases.items():
			target = fake_target()
			with self.subTest(case), self.assertRaises(store.LockError):
				store.put_locked(target, "k", b"x", mode, until)
			self.assertEqual(target._client.calls, [])

	def test_put_locked_needs_key_and_bytes(self):
		target = fake_target()
		for key, body in (("", b"x"), ("k", "text")):
			with self.subTest(key=key), self.assertRaises(store.LockError):
				store.put_locked(target, key, body, "GOVERNANCE", future())
		self.assertEqual(target._client.calls, [])

	def test_head_returns_none_for_missing_object(self):
		target = fake_target()
		self.assertIsNone(store.head(target, "missing"))
		store.put_locked(target, "k", b"x", "GOVERNANCE", future())
		self.assertEqual(store.head(target, "k")["Metadata"]["sha256"], hashlib.sha256(b"x").hexdigest())

	def test_head_raises_other_errors(self):
		target = fake_target(fake=LockedFakeS3(fail={"head_object"}))
		with self.assertRaises(Exception):
			store.head(target, "k")

	def test_read_sha256_hashes_the_stored_bytes(self):
		target = fake_target()
		store.put_locked(target, "k", b"stored bytes", "GOVERNANCE", future())
		version = store.head(target, "k")["VersionId"]
		self.assertEqual(store.read_sha256(target, "k", version), hashlib.sha256(b"stored bytes").hexdigest())
		with self.assertRaises(store.LockError):
			store.read_sha256(target, "k", None)

	def test_smoke_delete_only_for_smoke_objects(self):
		target = fake_target()
		smoke_key = "_smoke/20261025T010203Z-0123abcd.txt"
		for key, version in (
			("doc.pdf", "v1"),
			("_smoke/../doc.pdf", "v1"),
			("_smoke/anything.txt", "v1"),
			(smoke_key + ".pdf", "v1"),
			(smoke_key, None),
			(smoke_key, ""),
		):
			with self.subTest(key=key, version=version), self.assertRaises(store.LockError):
				store.smoke_delete(target, key, version)
		self.assertNotIn("delete_object", [call[0] for call in target._client.calls])

	def test_only_lock_errors_count_as_refused(self):
		from docarchive.tests.fake_s3 import error

		self.assertTrue(store.refused(error("AccessDenied", "DeleteObject")))
		self.assertFalse(store.refused(error("NoSuchKey", "DeleteObject")))
		self.assertFalse(store.refused(error("InvalidRequest", "DeleteObject")))
		self.assertFalse(store.refused(RuntimeError("network")))

	def test_verify_checks_mode_date_and_sha(self):
		target = fake_target()
		until = future(30)
		version = store.put_locked(target, "k", b"x", "GOVERNANCE", until)["version_id"]
		sha = hashlib.sha256(b"x").hexdigest()
		store.verify(target, "k", version, sha, "GOVERNANCE", until)
		cases = {
			"mode": ("COMPLIANCE", until, sha),
			"later date": ("GOVERNANCE", until + timedelta(days=1), sha),
			"sha": ("GOVERNANCE", until, "0" * 64),
		}
		for case, (mode, date, digest) in cases.items():
			with self.subTest(case), self.assertRaises(store.VerifyError):
				store.verify(target, "k", version, digest, mode, date)

	def test_store_has_no_general_delete(self):
		self.assertEqual([name for name in dir(store) if "delete" in name.lower()], ["smoke_delete"])

	def test_only_store_reaches_the_raw_client(self):
		modules = list(app_modules())
		self.assertIn("store.py", dict(modules))
		self.assertEqual(violations(modules), [])

	def test_guard_detects_other_callers(self):
		samples = {
			"put elsewhere": (
				"run.py",
				"def sneaky(t):\n    t._client.put_object(Bucket='b', Key='k', Body=b'')\n",
			),
			"getattr": ("api.py", "def sneaky(c):\n    getattr(c, 'put_object')()\n"),
			"wrong function": (
				"store.py",
				"def head(t):\n    t._client.put_object(Bucket='b', Key='k', Body=b'')\n",
			),
			"head elsewhere": (
				"store.py",
				"def put_locked(t):\n    t._client.head_object(Bucket='b', Key='k')\n",
			),
			"copy": ("store.py", "def put_locked(t):\n    t._client.copy_object(Bucket='b')\n"),
			"multipart": (
				"store.py",
				"def put_locked(t):\n    t._client.create_multipart_upload(Bucket='b')\n",
			),
			"presign": ("store.py", "def head(t):\n    t._client.generate_presigned_url('get_object')\n"),
			"legal hold": ("store.py", "def head(t):\n    t._client.put_object_legal_hold(Bucket='b')\n"),
			"bypass": (
				"store.py",
				"def put_locked(t):\n    t._client.put_object(BypassGovernanceRetention=True)\n",
			),
			"retention write": ("store.py", "def retention(t):\n    t._client.put_object_retention()\n"),
			"client import": ("run.py", "from s3storage.client import get_client\n"),
			"client module": ("run.py", "from s3storage import client\n"),
			"boto3": ("api.py", "import boto3\n"),
			"raw client": ("run.py", "def f(t):\n    return t._client\n"),
		}
		for case, (path, source) in samples.items():
			with self.subTest(case):
				self.assertTrue(violations([(path, ast.parse(source))]))
