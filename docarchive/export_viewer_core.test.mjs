import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const ViewerCore = createRequire(import.meta.url)("./export_viewer_core.js");
const { action, escape, move, cycle, typing, control, meta, outcomeOf, auditRows, entriesOf, threadOf } = ViewerCore;

const key = (value, extra = {}) => ({ key: value, ...extra });

test("keys map to actions outside inputs", () => {
	const cases = {
		j: "next",
		ArrowDown: "next",
		k: "previous",
		ArrowUp: "previous",
		Enter: "open",
		"/": "search",
		Escape: "escape",
		ArrowRight: "next_file",
		ArrowLeft: "previous_file",
		"?": "help",
		x: null,
	};
	for (const [name, expected] of Object.entries(cases)) assert.equal(action(key(name)), expected, name);
});

test("no shortcut fires while typing, except Escape", () => {
	for (const name of ["j", "k", "/", "?", "Enter", "ArrowLeft", "ArrowDown"]) {
		assert.equal(action(key(name), { typing: true }), null, name);
	}
	assert.equal(action(key("Escape"), { typing: true }), "escape");
});

test("modifiers, focused controls and the help overlay", () => {
	assert.equal(action(key("j", { ctrlKey: true })), null);
	assert.equal(action(key("ArrowLeft", { altKey: true })), null);
	assert.equal(action(key("k", { metaKey: true })), null);
	assert.equal(action(key("Enter"), { control: true }), null);
	assert.equal(action(key("j"), { control: true }), "next");
	assert.equal(action(key("Escape"), { help: true }), "close_help");
	assert.equal(action(key("?"), { help: true }), "close_help");
	assert.equal(action(key("j"), { help: true }), null);
});

test("escape clears, blurs or closes in order", () => {
	assert.equal(escape({ help: true, search: true, typing: true, preview: true }), "close_help");
	assert.equal(escape({ search: true, typing: true, preview: true }), "clear_search");
	assert.equal(escape({ typing: true, preview: true }), "blur");
	assert.equal(escape({ preview: true }), "close_preview");
	assert.equal(escape({}), null);
});

test("typing and control detection", () => {
	assert.equal(typing({ tagName: "INPUT", type: "search" }), true);
	assert.equal(typing({ tagName: "INPUT" }), true);
	assert.equal(typing({ tagName: "INPUT", type: "checkbox" }), false);
	assert.equal(typing({ tagName: "SELECT" }), true);
	assert.equal(typing({ tagName: "TEXTAREA" }), true);
	assert.equal(typing({ tagName: "DIV", isContentEditable: true }), true);
	assert.equal(typing({ tagName: "DIV" }), false);
	assert.equal(typing(null), false);
	assert.equal(control({ tagName: "BUTTON" }), true);
	assert.equal(control({ tagName: "DIV" }), false);
});

test("moving through the list stops at the edges", () => {
	const ids = ["a", "b", "c"];
	assert.equal(move(ids, "a", 1), "b");
	assert.equal(move(ids, "c", 1), "c");
	assert.equal(move(ids, "a", -1), "a");
	assert.equal(move(ids, null, 1), "a");
	assert.equal(move(ids, "gone", -1), "c");
	assert.equal(move([], "a", 1), null);
});

test("attachments wrap around", () => {
	assert.equal(cycle(3, 2, 1), 0);
	assert.equal(cycle(3, 0, -1), 2);
	assert.equal(cycle(1, 0, 1), 0);
	assert.equal(cycle(0, 0, 1), 0);
});

test("metadata always lists the same fields", () => {
	const L = { type: "T", date: "D", number: "N", counterparty: "C", counterparty_type: "CT", status: "S", retain_until: "R", legal_hold: "L", own_company: "O", lock_mode: "M", verified: "ok", problem: "bad", yes: "yes" };
	const empty = meta({ id: "x", verified: false }, L);
	const full = meta({ id: "y", type: "Rechnung", date: "2026-01-01", number: "1", counterparty: { id: "ACME", doctype: "Customer", type_label: "Kunde" }, verified: true, retain_until: "2036", legal_hold: true, own_company: "Own", lock_mode: "COMPLIANCE" }, L);
	assert.deepEqual(empty.map(([k]) => k), full.map(([k]) => k));
	assert.equal(empty.length, 10);
	assert.deepEqual(empty.filter(([, , v]) => v).map(([k]) => k), ["status"]);
	assert.deepEqual(full.find(([k]) => k === "legal_hold")[2], "yes");
	assert.deepEqual(full.find(([k]) => k === "counterparty")[2], "ACME");
	assert.deepEqual(full.find(([k]) => k === "counterparty_type")[2], "Kunde");
});

test("counterparty shows as name (id)", () => {
	const { party, partyType } = ViewerCore;
	assert.equal(party({ counterparty: { id: "C-1", name: "Acme", label: "Acme (C-1)" } }), "Acme (C-1)");
	assert.equal(party({ counterparty: { id: "C-1", name: "Acme" } }), "Acme (C-1)");
	assert.equal(party({ counterparty: { id: "C-1", name: "C-1" } }), "C-1");
	assert.equal(party({ counterparty: { id: "C-1" } }), "C-1");
	assert.equal(party({}), "");
	assert.equal(partyType({ counterparty: { doctype: "Customer" } }), "Customer");
});

const entries = [
	{ id: "a", at: "2026-01-02T00:00:00Z", user: "x@example.test", action: "archive.run", documents: [{ id: "DOC-1", outcome: "archived" }, { id: "DOC-2", outcome: "failed", reason: "checksum" }] },
	{ id: "b", at: "2026-03-01T00:00:00Z", user: "y@example.test", action: "legal_hold.set", documents: [{ id: "DOC-1", outcome: "held" }] },
	{ id: "c", at: "2026-02-01T00:00:00Z", user: "x@example.test", action: "settings.changed", documents: [] },
];

test("audit rows are filtered and newest first", () => {
	assert.deepEqual(auditRows(entries).map((e) => e.id), ["b", "c", "a"]);
	assert.deepEqual(auditRows(entries, { action: "archive.run" }).map((e) => e.id), ["a"]);
	assert.deepEqual(auditRows(entries, { search: "doc-2" }).map((e) => e.id), ["a"]);
	assert.deepEqual(auditRows(entries, { search: "y@" }).map((e) => e.id), ["b"]);
});

test("a document links to its entries and their outcome", () => {
	const doc = { id: "DOC-1", audit: ["a", "b", "missing"] };
	assert.deepEqual(entriesOf(doc, entries).map((e) => e.id), ["b", "a"]);
	assert.equal(outcomeOf(entries[0], "DOC-2"), "failed: checksum");
	assert.equal(outcomeOf(entries[0], "DOC-9"), "");
	assert.deepEqual(entriesOf({ id: "DOC-3" }, entries), []);
});

test("threads list the newest mail first", () => {
	const docs = [
		{ id: "a", thread_key: "t", correspondence_at: "2026-03-10T09:00:00Z" },
		{ id: "c", thread_key: "t", correspondence_at: "2026-03-12T09:00:00Z" },
		{ id: "x", thread_key: "other", correspondence_at: "2026-03-13T09:00:00Z" },
		{ id: "b", thread_key: "t", date: "2026-03-11" },
	];
	assert.deepEqual(threadOf(docs[0], docs).map((doc) => doc.id), ["c", "b", "a"]);
	assert.deepEqual(threadOf({ id: "n" }, docs), []);
});
