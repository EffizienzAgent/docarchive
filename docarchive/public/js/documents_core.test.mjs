import test from "node:test";
import assert from "node:assert/strict";
import {
	date_from_file_name,
	document_state,
	filter_documents,
	filter_options,
	load_filters,
	save_filters,
} from "./documents_core.js";

const TODAY = new Date(2026, 9, 25);
const parse = (name) => date_from_file_name(name, TODAY);

test("reads ISO dates and strips them from the title", () => {
	assert.deepEqual(parse("2026-08-05 Rechnung Acme.pdf"), {
		date: "2026-08-05",
		title: "Rechnung Acme",
	});
});

test("reads underscore, compact and German dates", () => {
	assert.deepEqual(parse("Rechnung_2026_08_05_Acme.pdf"), { date: "2026-08-05", title: "Rechnung Acme" });
	assert.deepEqual(parse("20260805-Vertrag.pdf"), { date: "2026-08-05", title: "Vertrag" });
	assert.deepEqual(parse("Mahnung 05.08.2026.pdf"), { date: "2026-08-05", title: "Mahnung" });
});

test("takes the first valid date", () => {
	assert.equal(parse("2026-01-02 bis 2026-03-04.pdf").date, "2026-01-02");
	assert.equal(parse("2026-13-40 und 2026-03-04.pdf").date, "2026-03-04");
});

test("ignores invalid dates and years out of range", () => {
	assert.equal(parse("20260231.pdf").date, null);
	assert.equal(parse("19891231.pdf").date, null);
	assert.equal(parse("20281231.pdf").date, null);
	assert.equal(parse("20271231.pdf").date, "2027-12-31");
	assert.equal(parse("Order 1234567890.pdf").date, null);
	assert.equal(parse("x2026-08-051.pdf").date, null);
});

test("mixed separators do not count", () => {
	assert.equal(parse("2026-08_05.pdf").date, null);
});

test("without a date the title is the cleaned file name", () => {
	assert.deepEqual(parse("Contract_acme-2026.pdf"), { date: null, title: "Contract acme 2026" });
});

test("a file name that is only a date keeps it as title", () => {
	assert.deepEqual(parse("2026-08-05.pdf"), { date: "2026-08-05", title: "2026 08 05" });
});

test("document state follows status and missing fields", () => {
	assert.equal(document_state({ archive_status: "Archived" }), "archived");
	assert.equal(document_state({ archive_status: "Failed", missing: [] }), "failed");
	assert.equal(document_state({ archive_status: "Pending", missing: ["File"] }), "open");
	assert.equal(document_state({ archive_status: "Pending", missing: [] }), "complete");
});

const DOCS = [
	{ name: "a", title: "Rechnung Acme", document_number: "RE-1", document_type: "Invoice", document_date: "2025-03-01", archive_status: "Archived" },
	{ name: "b", title: "Vertrag", document_number: "", document_type: "Contract", document_date: "2026-01-10", missing: ["Contract End Date"] },
	{ name: "c", title: "Gutschrift", document_number: "GS-7", document_type: "Invoice", document_date: "2026-05-02", missing: [] },
];
const names = (rows) => rows.map((row) => row.name);

test("sorts newest first unless asked otherwise", () => {
	assert.deepEqual(names(filter_documents(DOCS)), ["c", "b", "a"]);
	assert.deepEqual(names(filter_documents(DOCS, { oldest_first: true })), ["a", "b", "c"]);
});

test("filters by search, type, year and state", () => {
	assert.deepEqual(names(filter_documents(DOCS, { search: "gs-7" })), ["c"]);
	assert.deepEqual(names(filter_documents(DOCS, { search: "acme" })), ["a"]);
	assert.deepEqual(names(filter_documents(DOCS, { document_type: "Invoice" })), ["c", "a"]);
	assert.deepEqual(names(filter_documents(DOCS, { year: "2026" })), ["c", "b"]);
	assert.deepEqual(names(filter_documents(DOCS, { state: "open" })), ["b"]);
	assert.deepEqual(names(filter_documents(DOCS, { state: "complete", year: "2026" })), ["c"]);
});

test("filter options list types and years once", () => {
	assert.deepEqual(filter_options(DOCS), {
		document_types: ["Contract", "Invoice"],
		years: ["2026", "2025"],
	});
});

test("stored filters survive broken storage", () => {
	const broken = {
		getItem() {
			throw new Error("blocked");
		},
		setItem() {
			throw new Error("blocked");
		},
	};
	assert.deepEqual(load_filters(broken, "k"), {});
	save_filters(broken, "k", { year: "2026" });
	const memory = new Map();
	const storage = { getItem: (k) => memory.get(k) ?? null, setItem: (k, v) => memory.set(k, v) };
	assert.deepEqual(load_filters(storage, "k"), {});
	save_filters(storage, "k", { year: "2026" });
	assert.deepEqual(load_filters(storage, "k"), { year: "2026" });
});
