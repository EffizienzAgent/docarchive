const BASE_STYLE =
	"body{margin:0;padding:12px;font-family:system-ui,sans-serif;font-size:14px;color:#1f272e;background:#fff;overflow-wrap:anywhere}img{max-width:100%;height:auto}";

export function mail_document(html, remote_images = false) {
	const images = remote_images ? "data: https: http:" : "data:";
	const policy = `default-src 'none'; style-src 'unsafe-inline'; img-src ${images}`;
	return `<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="${policy}">
<meta name="referrer" content="no-referrer"><base target="_blank">
<style>${BASE_STYLE}</style></head><body>${html || ""}</body></html>`;
}

export const is_eml = (file_name) => /\.eml$/i.test(String(file_name || ""));

export function size_label(bytes) {
	if (bytes < 1024) return `${bytes} B`;
	if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
	return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

const HTML_ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

export const escape_html = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => HTML_ESCAPES[c]);

export const mail_title = (subject, fallback) => escape_html(subject || fallback);

export function safe_file_name(file_name, fallback = "attachment") {
	const name = String(file_name || "")
		.replace(/[\u0000-\u001f\u007f<>"'&`]/g, "")
		.trim();
	return name || fallback;
}

export function archive_lines(result, { link, archived_as }) {
	return [
		...Object.entries(result.archived).map(
			([name, doc]) => `${escape_html(name)}: ${archived_as(link(doc))}`,
		),
		...Object.entries(result.refused).map(([name, reason]) => `${escape_html(name)}: ${escape_html(reason)}`),
	];
}

export function original_actions(status, can_create) {
	if (status.archived) return { headline: true, show: status.viewable };
	return {
		headline: null,
		show: status.viewable,
		archive: can_create,
		archive_reason: status.reason || null,
		ignore: can_create && status.viewable && !status.ignored,
		unignore: can_create && status.viewable && status.ignored,
	};
}

export function archive_headline(state, { link, date, t }) {
	if (!state.business_document) return { html: escape_html(t("This e-mail is filed.")), color: "blue" };
	const doc = link(state.business_document);
	if (state.archive_status === "Archived") {
		const html = state.retain_until
			? t("Archived as {0} until {1}", [doc, escape_html(date(state.retain_until))])
			: t("Archived as {0}", [doc]);
		return { html, color: "green" };
	}
	if (state.archive_status === "Deleted") return { html: `${doc} · ${escape_html(t("Deleted"))}`, color: "gray" };
	if (state.archive_status === "Failed") return { html: t("Archiving {0} failed", [doc]), color: "red" };
	return { html: t("Filed as {0}, not yet archived", [doc]), color: "orange" };
}

const BADGES = {
	archived: { color: "green", label: (t) => t("Archived") },
	filed: { color: "blue", label: (t) => t("Filed") },
	suggested: { color: "yellow", label: (t) => t("Should be archived") },
	not_archived: { color: "gray", label: (t) => t("Not archived") },
	ignored: { color: "gray", label: (t) => t("Ignored") },
};

export function archive_badge(status, { t, link }) {
	const badge = BADGES[status.state];
	if (!badge) return null;
	const failed = status.state === "filed" && status.archive_status === "Failed";
	const label = escape_html(failed ? t("Failed") : badge.label(t));
	const linked = status.business_document && ["archived", "filed"].includes(status.state);
	const actions = [];
	if (status.state === "filed" && status.can_discard) actions.push("discard");
	if (status.state === "ignored" && status.can_archive) actions.push("unignore");
	if (["suggested", "not_archived", "ignored"].includes(status.state) && status.can_archive && !status.reason) {
		actions.push("archive");
	}
	return {
		color: failed ? "red" : badge.color,
		label_html: linked ? `${label} · ${link(status.business_document)}` : label,
		actions,
	};
}

const action_button = (action, label) =>
	`<button class="btn btn-xs btn-primary" data-docarchive-action="${action}">${escape_html(label)}</button>`;

const action_link = (action, label) =>
	`<a href="#" data-docarchive-action="${action}">${escape_html(label)}</a>`;

const action_link_button = (action, label) =>
	`<button class="btn btn-xs btn-link" data-docarchive-action="${action}">${escape_html(label)}</button>`;

function reason_text(reason, t) {
	const [kind, value] = String(reason).split(/:(.*)/s);
	if (kind === "keyword") return t("subject mentions “{0}”", [escape_html(value)]);
	if (kind === "attachment" && value === "pdf") return t("PDF attachment");
	if (kind === "attachment" && value === "xml") return t("XML attachment");
	return escape_html(reason);
}

const short_rule = (rule) => String(rule ?? "").split(/\.\s/)[0];

function suggestion_bar(status, { t, date, rule }) {
	const headline = [];
	if (status.state === "suggested" && status.suggestion_reasons?.length) {
		const reasons = status.suggestion_reasons.map((reason) => reason_text(reason, t)).join(", ");
		headline.push(`<strong>${t("Suggested: {0}", [reasons])}</strong>`);
	}
	if (status.expires_on) headline.push(t("The original is removed on {0}.", [escape_html(date(status.expires_on))]));
	const text = [];
	if (headline.length) text.push(`<div>${headline.join(" ")}</div>`);
	text.push(
		`<div class="text-muted small" title="${escape_html(rule)}">${escape_html(short_rule(rule))}</div>`,
	);
	const actions = [];
	if (status.can_archive && !status.reason) actions.push(action_button("archive", t("Archive")));
	if (status.can_archive) actions.push(action_link_button("ignore", t("Don't archive")));
	return {
		color: "yellow",
		html: `<div class="docarchive-bar"><div class="docarchive-bar-text">${text.join("")}</div><div class="docarchive-bar-actions">${actions.join("")}</div></div>`,
	};
}

function quiet_bar(status, { t }) {
	const label = status.automated ? t("Automatic e-mail") : t("Internal e-mail");
	const archive = status.can_archive && !status.reason ? action_link("archive", t("Archive")) : "";
	return { color: "gray", html: [escape_html(label), archive].join(" ") };
}

function ignored_bar(status, { t }) {
	const actions = status.can_archive
		? [action_link("unignore", t("Undo")), action_link("archive", t("Archive"))]
		: [];
	return { color: "gray", html: [escape_html(t("Marked as not to be archived.")), ...actions].join(" ") };
}

function filed_bar(status, { t, link, date }) {
	const lines = [status.business_document ? t("Filed as {0}", [link(status.business_document)]) : escape_html(t("Filed"))];
	if (status.sealed_from) lines.push(t("Sealed on {0} at the earliest.", [escape_html(date(status.sealed_from))]));
	if (status.expires_on) lines.push(t("Counterparty missing, removed on {0}.", [escape_html(date(status.expires_on))]));
	if (status.can_discard) lines.push(action_button("discard", t("Discard")));
	return { color: "blue", html: lines.join(" ") };
}

export function state_bar(status, helpers) {
	switch (status.state) {
		case "suggested":
			return suggestion_bar(status, helpers);
		case "not_archived":
			return status.automated || status.internal ? quiet_bar(status, helpers) : suggestion_bar(status, helpers);
		case "ignored":
			return ignored_bar(status, helpers);
		case "filed":
			if (status.archive_status === "Failed" && status.business_document) return archive_headline(status, helpers);
			return filed_bar(status, helpers);
		case "archived":
			return archive_headline(status, helpers);
		default:
			return null;
	}
}
