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
