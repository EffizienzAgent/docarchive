import test from "node:test";
import assert from "node:assert/strict";
import { PREVIEW_FIELDS, preview_header } from "./documents_core.js";

const esc = (value) =>
	String(value ?? "").replace(/[&<>"']/g, (char) => `&#${char.charCodeAt(0)};`);
const options = { t: (text) => text, esc, value: (key, doc) => ({ text: doc[key] || "" }), actions: "<a>Open</a>" };
const fields = (html) => [...html.matchAll(/data-field="([^"]+)"/g)].map((match) => match[1]);

test("the header renders every field in the same order, even when empty", () => {
	const empty = preview_header({ name: "DOC-1" }, options);
	const full = preview_header(
		{ name: "DOC-2", title: "Rechnung", document_type: "Rechnung", document_number: "RE-1", counterparty: "ACME" },
		options,
	);
	const keys = PREVIEW_FIELDS.map(([key]) => key);
	assert.deepEqual(fields(empty), keys);
	assert.deepEqual(fields(full), keys);
	assert.equal(empty.match(/docarchive-empty">–</g).length, keys.length);
	for (const [, label] of PREVIEW_FIELDS) assert.ok(empty.includes(label));
	assert.ok(empty.includes("<a>Open</a>"));
	assert.ok(empty.includes('title="DOC-1"'));
});

test("the header escapes the title and values", () => {
	const html = preview_header(
		{ name: "D", title: '<img src=x onerror="a">', document_number: "<b>1</b>" },
		options,
	);
	assert.ok(!html.includes("<img"));
	assert.ok(!html.includes("<b>1</b>"));
	assert.ok(html.includes('title="&#60;b&#62;1&#60;/b&#62;"'));
});
