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
