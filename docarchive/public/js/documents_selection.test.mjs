import test from "node:test";
import assert from "node:assert/strict";
import {
	bulk_actions,
	bulk_targets,
	export_defaults,
	preview_key,
	prune_selection,
	selection_state,
	step,
	toggle_all,
	viewer_kind,
} from "./documents_core.js";

const ALL = { extend: true, hold: true, export: true };
const ready = { name: "A", eligible: true, archive_status: "Pending", document_date: "2026-02-01" };
const archived = { name: "B", archive_status: "Archived", document_type: "Rechnung", document_date: "2026-03-05" };
const held = { name: "C", archive_status: "Archived", legal_hold: 1 };
const open = { name: "D", archive_status: "Pending", eligible: false };
const deleted = { name: "E", archive_status: "Deleted" };
const rows = [ready, archived, held, open, deleted];

test("bulk actions depend on the document and the user's access", () => {
	assert.deepEqual(bulk_actions(ready, ALL), ["archive"]);
	assert.deepEqual(bulk_actions(archived, ALL), ["extend", "hold", "export"]);
	assert.deepEqual(bulk_actions(held, ALL), ["extend", "export"]);
	assert.deepEqual(bulk_actions(archived, {}), []);
	assert.deepEqual(bulk_actions(open, ALL), []);
	assert.deepEqual(bulk_actions({ ...deleted, eligible: true }, ALL), []);
});

test("selection state counts only selectable rows and reports indeterminate", () => {
	const none = selection_state(rows, new Set(), ALL);
	assert.deepEqual(none, { selectable: ["A", "B", "C"], count: 0, all: false, indeterminate: false });
	const some = selection_state(rows, new Set(["A", "D"]), ALL);
	assert.deepEqual([some.count, some.all, some.indeterminate], [1, false, true]);
	const all = selection_state(rows, new Set(["A", "B", "C"]), ALL);
	assert.deepEqual([all.count, all.all, all.indeterminate], [3, true, false]);
	assert.deepEqual(selection_state([open, deleted], new Set(), ALL).selectable, []);
});

test("select all toggles every visible selectable row", () => {
	assert.deepEqual([...toggle_all(rows, new Set(), ALL, true)].sort(), ["A", "B", "C"]);
	assert.deepEqual([...toggle_all(rows, new Set(["A"]), {}, true)], ["A"]);
	assert.equal(toggle_all(rows, new Set(["A", "B"]), ALL, false).size, 0);
});

test("pruning drops hidden and no longer selectable rows", () => {
	const selected = new Set(["A", "B", "D", "X"]);
	assert.deepEqual([...prune_selection([ready, open], selected, ALL)], ["A"]);
	assert.deepEqual([...prune_selection(rows, selected, {})], ["A"]);
});

test("bulk targets list the selected rows per action", () => {
	const targets = bulk_targets(rows, new Set(["A", "B", "C"]), ALL);
	assert.deepEqual(targets, { archive: ["A"], extend: ["B", "C"], hold: ["B"], export: ["B", "C"] });
});

test("export defaults cover the selected range and a shared type", () => {
	const party = { counterparty_type: "Organization", counterparty: "ACME" };
	assert.deepEqual(export_defaults(party, [archived, { ...archived, name: "F", document_date: "2025-12-01" }]), {
		...party,
		date_from: "2025-12-01",
		date_to: "2026-03-05",
		document_type: "Rechnung",
	});
	assert.deepEqual(export_defaults(party, [held]), party);
});

test("preview keys", () => {
	assert.equal(preview_key({ key: "ArrowDown" }), "next");
	assert.equal(preview_key({ key: "j" }), "next");
	assert.equal(preview_key({ key: "ArrowUp" }), "previous");
	assert.equal(preview_key({ key: "k" }), "previous");
	assert.equal(preview_key({ key: "Escape" }), "close");
	assert.equal(preview_key({ key: "j", ctrlKey: true }), null);
	assert.equal(preview_key({ key: "ArrowDown", metaKey: true }), null);
	assert.equal(preview_key({ key: "x" }), null);
});

test("step moves within the visible rows and stops at the edges", () => {
	const names = ["A", "B", "C"];
	assert.equal(step(names, "A", 1), "B");
	assert.equal(step(names, "C", 1), "C");
	assert.equal(step(names, "B", -1), "A");
	assert.equal(step(names, "A", -1), "A");
	assert.equal(step(names, "gone", 1), "A");
	assert.equal(step([], "A", 1), null);
});

test("viewer kind only previews local files", () => {
	assert.equal(viewer_kind("/private/files/a.pdf"), "pdf");
	assert.equal(viewer_kind("/files/b.PNG"), "image");
	assert.equal(viewer_kind("/private/files/c.html"), "html");
	assert.equal(viewer_kind("/private/files/d.htm?x=1"), "html");
	assert.equal(viewer_kind("https://evil.example/e.html"), "other");
	assert.equal(viewer_kind("/private/files/../f.html"), "other");
	assert.equal(viewer_kind("/private/files/g.svg"), "other");
	assert.equal(viewer_kind("/private/files/h.xml"), "other");
	assert.equal(viewer_kind(null), "other");
});
