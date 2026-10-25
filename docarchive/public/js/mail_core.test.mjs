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
	assert.deepEqual(archive_headline({ business_document: null }, helpers), {
		html: "This e-mail is archived.",
		color: "blue",
	});
});
