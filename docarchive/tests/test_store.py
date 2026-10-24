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
GUARDED = {
	"put_object": "put_locked",
	"delete_object": "smoke_delete",
	"delete_objects": None,
	"put_object_retention": "extend_locked",
}


def future(days=1):
	return datetime.now(UTC) + timedelta(days=days)


def fake_target(name="primary", fake=None, lock_mode="GOVERNANCE"):
	return store.Target(name, f"archive-{name}", lock_mode, fake or LockedFakeS3())


def app_modules():
	for path in APP_DIR.rglob("*.py"):
		relative = path.relative_to(APP_DIR)
		if "tests" not in relative.parts:
			yield relative.as_posix(), ast.parse(path.read_text(encoding="utf-8"))


def uses_in(path, tree):
	functions = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
	for node in ast.walk(tree):
		if isinstance(node, ast.Attribute):
			name = node.attr
		elif isinstance(node, ast.Constant) and isinstance(node.value, str):
			name = node.value
		else:
			continue
		if name in GUARDED or name == "_client":
			owner = next(
				(f.name for f in functions if f.lineno <= node.lineno <= (f.end_lineno or f.lineno)), None
			)
			yield path, owner, name


def violations(modules):
	return [
		(path, owner, name)
		for path, tree in modules
		for path, owner, name in uses_in(path, tree)
		if path != "store.py" or (name != "_client" and owner != GUARDED[name])
	]


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
		self.assertEqual(store.read_sha256(target, "k"), hashlib.sha256(b"stored bytes").hexdigest())

	def test_smoke_delete_only_for_smoke_objects(self):
		target = fake_target()
		store.put_locked(target, "doc.pdf", b"x", "GOVERNANCE", future())
		with self.assertRaises(store.LockError):
			store.smoke_delete(target, "doc.pdf", "v1")
		self.assertNotIn("delete_object", [call[0] for call in target._client.calls])

	def test_store_has_no_general_delete(self):
		self.assertEqual([name for name in dir(store) if "delete" in name.lower()], ["smoke_delete"])

	def test_only_store_reaches_the_raw_client(self):
		modules = list(app_modules())
		self.assertIn("store.py", dict(modules))
		self.assertEqual(violations(modules), [])

	def test_guard_detects_other_callers(self):
		samples = {
			"run.py": "def sneaky(t):\n    t._client.put_object(Bucket='b', Key='k', Body=b'')\n",
			"api.py": "def sneaky(c):\n    getattr(c, 'put_object')()\n",
			"store.py": "def head(t):\n    t._client.put_object(Bucket='b', Key='k', Body=b'')\n",
		}
		for path, source in samples.items():
			with self.subTest(path):
				self.assertTrue(violations([(path, ast.parse(source))]))
