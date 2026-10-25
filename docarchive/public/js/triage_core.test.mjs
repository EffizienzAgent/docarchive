import test from "node:test";
import assert from "node:assert/strict";
import { Queue, changed_values, initial_date, key_action, local_file_url, preview_kind } from "./triage_core.js";

test("keyboard shortcuts", () => {
	assert.equal(key_action({ key: "Enter", ctrlKey: true }), "save_next");
	assert.equal(key_action({ key: "Enter", metaKey: true }), "save_next");
	assert.equal(key_action({ key: "Enter" }), null);
	assert.equal(key_action({ key: "ArrowLeft", altKey: true }), "previous");
	assert.equal(key_action({ key: "ArrowRight", altKey: true }), "next");
	assert.equal(key_action({ key: "ArrowRight" }), null);
	assert.equal(key_action({ key: "Escape" }), "close");
});

test("queue moves within bounds", () => {
	const queue = new Queue(["a", "b", "c"], 1);
	assert.deepEqual(queue.position, { current: 2, total: 3 });
	assert.equal(queue.upcoming, "c");
	assert.equal(queue.next(), true);
	assert.equal(queue.current, "c");
	assert.equal(queue.is_last, true);
	assert.equal(queue.next(), false);
	assert.equal(queue.upcoming, undefined);
	assert.equal(queue.previous(), true);
	assert.equal(queue.previous(), true);
	assert.equal(queue.previous(), false);
	assert.equal(queue.current, "a");
});

test("queue drops duplicates and clamps the start", () => {
	assert.deepEqual(new Queue(["a", "a", "b"], 9).position, { current: 2, total: 2 });
	assert.deepEqual(new Queue(["a"], -3).position, { current: 1, total: 1 });
});

test("only local files are previewed inline", () => {
	assert.equal(preview_kind("/private/files/Rechnung.pdf"), "pdf");
	assert.equal(preview_kind("/files/scan.JPG"), "image");
	assert.equal(preview_kind("/private/files/page.html"), "other");
	assert.equal(preview_kind("/private/files/doc.svg"), "other");
	assert.equal(preview_kind("https://evil.example/x.pdf"), "other");
	assert.equal(preview_kind("//evil.example/files/x.pdf"), "other");
	assert.equal(local_file_url("/private/files/../x.pdf"), null);
	assert.equal(local_file_url("/private/files/%2e%2e/x.pdf"), null);
	assert.equal(local_file_url("/private/files/a%5Cb.pdf"), null);
	assert.equal(local_file_url(null), null);
});

test("the date falls back to the file name", () => {
	assert.equal(initial_date({ document_date: "2026-01-02", suggested_date: "2026-08-05" }), "2026-01-02");
	assert.equal(initial_date({ document_date: null, suggested_date: "2026-08-05" }), "2026-08-05");
	assert.equal(initial_date({}), null);
});

test("only changed fields are sent", () => {
	const original = { title: "A", document_number: null, document_date: "2026-01-02", counterparty: "ORG-1" };
	assert.deepEqual(
		changed_values(original, { title: "A", document_number: "", document_date: "2026-01-02", counterparty: "ORG-1" }),
		{},
	);
	assert.deepEqual(
		changed_values(original, { title: "B", document_number: "RE-1", document_date: "2026-01-02", counterparty: null }),
		{ title: "B", document_number: "RE-1", counterparty: null },
	);
});

test("removing the current item keeps the position", () => {
	const queue = new Queue(["a", "b", "c"], 1);
	assert.equal(queue.remove_current(), true);
	assert.equal(queue.current, "c");
	assert.equal(queue.remove_current(), true);
	assert.equal(queue.current, "a");
	assert.equal(queue.remove_current(), false);
	assert.equal(queue.current, undefined);
});
