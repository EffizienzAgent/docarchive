import test from "node:test";
import assert from "node:assert/strict";
import { escape_html, is_eml, mail_document, mail_title, safe_file_name, size_label } from "./mail_core.js";

test("remote images are blocked by policy until asked for", () => {
	assert.match(mail_document("<p>Hi</p>"), /img-src data:"/);
	assert.match(mail_document("<p>Hi</p>", true), /img-src data: https: http:"/);
});

test("the document allows no scripts or remote loads by policy", () => {
	const doc = mail_document("<b>x</b>");
	assert.match(doc, /default-src 'none'/);
	assert.doesNotMatch(doc, /script-src/);
	assert.match(doc, /no-referrer/);
	assert.match(doc, /<body><b>x<\/b><\/body>/);
	assert.match(mail_document(null), /<body><\/body>/);
});

test("eml detection and sizes", () => {
	assert.ok(is_eml("Mail.EML"));
	assert.ok(!is_eml("mail.eml.pdf"));
	assert.ok(!is_eml(undefined));
	assert.equal(size_label(512), "512 B");
	assert.equal(size_label(2048), "2.0 KB");
	assert.equal(size_label(3 * 1024 * 1024), "3.0 MB");
});

test("the dialog title escapes the subject", () => {
	const title = mail_title("<img src=x onerror=alert(1)>", "(no subject)");
	assert.equal(title, "&lt;img src=x onerror=alert(1)&gt;");
	assert.equal(mail_title("", "(no <subject>)"), "(no &lt;subject&gt;)");
	assert.equal(escape_html(`"'&`), "&quot;&#39;&amp;");
});

test("attachment file names lose markup characters", () => {
	assert.equal(safe_file_name('<img src=x onerror="alert(1)">.pdf'), "img src=x onerror=alert(1).pdf");
	assert.equal(safe_file_name("Rechnung 2026.pdf"), "Rechnung 2026.pdf");
	assert.equal(safe_file_name("<>"), "attachment");
});

test("archive results are escaped line by line", async () => {
	const { archive_lines } = await import("./mail_core.js");
	const lines = archive_lines(
		{ archived: { "<a>": "DOC-1" }, refused: { "MO-2": "<b>no</b>" } },
		{ link: (doc) => `[${doc}]`, archived_as: (link) => `Archived as ${link}` },
	);
	assert.deepEqual(lines, ["&lt;a&gt;: Archived as [DOC-1]", "MO-2: &lt;b&gt;no&lt;/b&gt;"]);
});

test("mail original actions follow its status", async () => {
	const { original_actions } = await import("./mail_core.js");
	const base = { archived: false, viewable: true, ignored: false, reason: null };
	assert.deepEqual(original_actions(base, true), {
		headline: null,
		show: true,
		archive: true,
		archive_reason: null,
		ignore: true,
		unignore: false,
	});
	assert.equal(original_actions({ ...base, ignored: true }, true).unignore, true);
	assert.equal(original_actions(base, false).archive, false);
	const pending = original_actions({ ...base, viewable: false, reason: "still being stored" }, true);
	assert.deepEqual([pending.show, pending.archive_reason, pending.ignore], [false, "still being stored", false]);
	assert.deepEqual(original_actions({ archived: true, business_document: "DOC-1", viewable: true }, true), {
		headline: true,
		show: true,
	});
});

test("the archive headline follows the document's status", async () => {
	const { archive_headline } = await import("./mail_core.js");
	const t = (text, args = []) => args.reduce((out, arg, index) => out.replace(`{${index}}`, arg), text);
	const helpers = { link: (name) => `<a>${name}</a>`, date: (value) => `on ${value}`, t };
	assert.deepEqual(archive_headline({ business_document: "DOC-1", archive_status: "Pending" }, helpers), {
		html: "Filed as <a>DOC-1</a>, not yet archived",
		color: "orange",
	});
	assert.deepEqual(
		archive_headline({ business_document: "DOC-1", archive_status: "Archived", retain_until: "<2036>" }, helpers),
		{ html: "Archived as <a>DOC-1</a> until on &lt;2036&gt;", color: "green" },
	);
	assert.equal(archive_headline({ business_document: "DOC-1", archive_status: "Failed" }, helpers).color, "red");
	assert.equal(archive_headline({ business_document: "DOC-1" }, helpers).color, "orange");
	assert.deepEqual(archive_headline({ business_document: "DOC-1", archive_status: "Archived" }, helpers), {
		html: "Archived as <a>DOC-1</a>",
		color: "green",
	});
	assert.deepEqual(archive_headline({ business_document: "DOC-1", archive_status: "Deleted" }, helpers), {
		html: "<a>DOC-1</a> · Deleted",
		color: "gray",
	});
	assert.deepEqual(archive_headline({ business_document: null }, helpers), {
		html: "This e-mail is filed.",
		color: "blue",
	});
});

const state_t = (text, args = []) => args.reduce((out, arg, index) => out.replace(`{${index}}`, arg), text);
const state_helpers = { t: state_t, link: (name) => `<a>${name}</a>`, date: (value) => value, rule: "R" };

test("badge per state", async () => {
	const { archive_badge } = await import("./mail_core.js");
	const h = state_helpers;
	assert.equal(archive_badge({ state: "none" }, h), null);
	assert.deepEqual(archive_badge({ state: "suggested", can_archive: true }, h).actions, ["archive"]);
	assert.equal(archive_badge({ state: "suggested", can_archive: true, reason: "pending" }, h).actions.length, 0);
	assert.deepEqual(archive_badge({ state: "filed", business_document: "D-1", can_discard: true }, h).actions, ["discard"]);
	assert.equal(archive_badge({ state: "suggested", can_archive: false }, h).actions.length, 0);
	assert.equal(archive_badge({ state: "archived", business_document: "D-1" }, h).color, "green");
	assert.deepEqual(archive_badge({ state: "ignored", can_archive: true }, h).actions, ["unignore", "archive"]);
	assert.equal(archive_badge({ state: "filed" }, h).label_html, "Filed");
	assert.match(archive_badge({ state: "filed", business_document: "D-1" }, h).label_html, /<a>D-1<\/a>/);
});

test("bar escapes reasons and names", async () => {
	const { state_bar } = await import("./mail_core.js");
	const bar = state_bar(
		{ state: "suggested", can_archive: true, suggestion_reasons: ["keyword:<b>x</b>", "attachment:pdf"] },
		state_helpers,
	);
	assert.match(bar.html, /&lt;b&gt;x&lt;\/b&gt;/);
	assert.match(bar.html, /PDF attachment/);
	assert.match(bar.html, /data-docarchive-action="archive"/);
	assert.match(bar.html, /data-docarchive-action="ignore"/);
	assert.equal(bar.color, "yellow");
});

test("suggestion bar shows the short rule with the full rule as tooltip", async () => {
	const { state_bar } = await import("./mail_core.js");
	const bar = state_bar(
		{ state: "suggested", can_archive: true, suggestion_reasons: ["keyword:Angebot"] },
		{ ...state_helpers, rule: 'Archive offers. Not: "news" <b>.' },
	);
	assert.match(bar.html, /<div class="text-muted small" title="Archive offers\. Not: &quot;news&quot; &lt;b&gt;\.">Archive offers<\/div>/);
	assert.doesNotMatch(bar.html.replace(/title="[^"]*"/, ""), /Not:/);
	assert.match(bar.html, /<strong>Suggested: subject mentions/);
});

test("automated mail gets a quiet gray line", async () => {
	const { state_bar } = await import("./mail_core.js");
	const bar = state_bar(
		{ state: "not_archived", automated: true, can_archive: true },
		{ ...state_helpers, rule: "RULE-TEXT" },
	);
	assert.equal(bar.color, "gray");
	assert.match(bar.html, /Automatic e-mail/);
	assert.doesNotMatch(bar.html, /RULE-TEXT/);
	assert.match(bar.html, /data-docarchive-action="archive"/);
});

test("ignored bar offers undo and archive", async () => {
	const { state_bar } = await import("./mail_core.js");
	const bar = state_bar({ state: "ignored", can_archive: true }, state_helpers);
	assert.match(bar.html, /data-docarchive-action="unignore"/);
	assert.match(bar.html, /data-docarchive-action="archive"/);
});

test("failed archive run is red in badge and bar", async () => {
	const { archive_badge, state_bar } = await import("./mail_core.js");
	const status = { state: "filed", business_document: "D-1", archive_status: "Failed" };
	assert.equal(archive_badge(status, state_helpers).color, "red");
	assert.equal(archive_badge(status, state_helpers).label_html, "Failed · <a>D-1</a>");
	const bar = state_bar(status, state_helpers);
	assert.equal(bar.color, "red");
	assert.match(bar.html, /D-1/);
});

test("filed bar shows seal date and discard", async () => {
	const { state_bar } = await import("./mail_core.js");
	const bar = state_bar(
		{ state: "filed", business_document: "D-1", sealed_from: "2026-10-30 02:30", can_discard: true },
		state_helpers,
	);
	assert.equal(bar.color, "blue");
	assert.match(bar.html, /Sealed on 2026-10-30 02:30 at the earliest/);
	assert.match(bar.html, /data-docarchive-action="discard"/);
});

test("filed bar without discard right has no button", async () => {
	const { state_bar } = await import("./mail_core.js");
	const bar = state_bar({ state: "filed", business_document: "D-1", expires_on: "2026-11-01" }, state_helpers);
	assert.doesNotMatch(bar.html, /data-docarchive-action/);
	assert.match(bar.html, /Counterparty missing, removed on 2026-11-01/);
});

test("archived delegates to archive_headline", async () => {
	const { state_bar, archive_headline } = await import("./mail_core.js");
	const status = { state: "archived", business_document: "D-1", archive_status: "Archived" };
	assert.deepEqual(state_bar(status, state_helpers), archive_headline(status, state_helpers));
	assert.equal(state_bar({ state: "none" }, state_helpers), null);
});
