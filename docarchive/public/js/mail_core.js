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
	if (status.archived) {
		return { headline: status.business_document ? "link" : "plain", show: status.viewable };
	}
	return {
		headline: null,
		show: status.viewable,
		archive: can_create,
		archive_reason: status.reason || null,
		ignore: can_create && status.viewable && !status.ignored,
		unignore: can_create && status.viewable && status.ignored,
	};
}
