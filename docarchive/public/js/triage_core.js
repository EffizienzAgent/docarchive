const IMAGE_EXTENSIONS = ["jpg", "jpeg", "png", "gif", "webp", "avif", "bmp"];

export function key_action(event) {
	if ((event.ctrlKey || event.metaKey) && event.key === "Enter") return "save_next";
	if (event.altKey && event.key === "ArrowLeft") return "previous";
	if (event.altKey && event.key === "ArrowRight") return "next";
	if (event.key === "Escape") return "close";
	return null;
}

export class Queue {
	constructor(names, start = 0) {
		this.names = [...new Set(names)];
		this.index = Math.min(Math.max(start, 0), Math.max(this.names.length - 1, 0));
	}

	get current() {
		return this.names[this.index];
	}

	get upcoming() {
		return this.names[this.index + 1];
	}

	get position() {
		return { current: this.index + 1, total: this.names.length };
	}

	get is_first() {
		return this.index === 0;
	}

	get is_last() {
		return this.index >= this.names.length - 1;
	}

	next() {
		if (this.is_last) return false;
		this.index += 1;
		return true;
	}

	previous() {
		if (this.is_first) return false;
		this.index -= 1;
		return true;
	}
}

export function local_file_url(file_url) {
	if (typeof file_url !== "string") return null;
	const path = file_url.split(/[?#]/)[0];
	if (!/^\/(private\/)?files\/[^/]/.test(path) || path.includes("//")) return null;
	let decoded;
	try {
		decoded = decodeURIComponent(path);
	} catch {
		return null;
	}
	if (/[\\\u0000-\u001f]/.test(decoded)) return null;
	if (decoded.split("/").some((part) => part === "." || part === "..")) return null;
	return file_url;
}

export function preview_kind(file_url) {
	if (!local_file_url(file_url)) return "other";
	const name = file_url.split(/[?#]/)[0].split("/").pop().toLowerCase();
	const extension = name.includes(".") ? name.split(".").pop() : "";
	if (extension === "pdf") return "pdf";
	if (IMAGE_EXTENSIONS.includes(extension)) return "image";
	return "other";
}

export const initial_date = (item) => item.document_date || item.suggested_date || null;
