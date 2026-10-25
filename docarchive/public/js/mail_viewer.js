import * as core from "./mail_core";

frappe.provide("docarchive");
docarchive.mail_core = core;

const VIEWER = "docarchive.mail.viewer";
const ARCHIVING = "docarchive.mail.archiving";
const INBOX = "docarchive.mail.inbox";
const DOCUMENT = "Business Document";
const ORIGINAL = "Mail Original";
const esc = (value) => frappe.utils.escape_html(value ?? "");
const doc_link = (name) =>
	`<a href="${esc(frappe.utils.get_form_link(DOCUMENT, name))}">${esc(name)}</a>`;

const HEADER_LABELS = () => [
	["from", __("From")],
	["to", __("To")],
	["cc", __("Cc")],
	["date", __("Date")],
	["subject", __("Subject")],
	["message_id", __("Message-ID")],
];

const ORIGINS = () => ({
	"frappe-receive": __("Received by Frappe"),
	"frappe-mail": __("Received through Frappe Mail"),
	"frappe-send": __("Rebuilt by Frappe after sending"),
	mailsync: __("Read from the mailbox by mailsync"),
	"manual-upload": __("Uploaded as .eml by a person"),
});

const GAP_STATES = () => ({
	archived: __("Archived in another thread"),
	captured: __("Original available"),
	no_original: __("In Frappe, no original"),
	unknown: __("Not in Frappe"),
});

function load_remote_images(html) {
	const doc = new DOMParser().parseFromString(html || "", "text/html");
	for (const img of doc.querySelectorAll("img[data-remote-src]")) {
		img.setAttribute("src", img.getAttribute("data-remote-src"));
		img.removeAttribute("data-remote-src");
	}
	return doc.body.innerHTML;
}

function headers_html(mail) {
	const rows = HEADER_LABELS()
		.filter(([key]) => mail.headers[key])
		.map(
			([key, label]) =>
				`<tr><td class="text-muted docarchive-nowrap">${esc(label)}</td><td>${esc(mail.headers[key])}</td></tr>`,
		)
		.join("");
	const integrity = mail.sha256_ok
		? `<span class="indicator-pill green">${esc(__("Checksum matches"))}</span>`
		: `<span class="indicator-pill red">${esc(__("Checksum does not match"))}</span>`;
	const origin = ORIGINS()[mail.origin] || mail.origin;
	return `<table class="table table-condensed table-borderless docarchive-mail-headers">${rows}</table>
		<p class="docarchive-mail-integrity">${integrity}
			${origin ? `<span class="text-muted small">${esc(__("Origin: {0}", [origin]))}</span>` : ""}</p>`;
}

function attachment_url(name, index, doctype = DOCUMENT) {
	const args = new URLSearchParams({ name, index, doctype });
	return `/api/method/${VIEWER}.attachment?${args}`;
}

function attachments_html(name, mail, can_file, doctype) {
	if (!mail.attachments.length) return "";
	const rows = mail.attachments
		.map(
			(file) => `<tr>
				<td>${esc(file.file_name)}</td>
				<td class="text-muted docarchive-nowrap">${esc(core.size_label(file.size))}</td>
				<td class="docarchive-actions">
					<a class="btn btn-xs btn-default" href="${esc(attachment_url(name, file.index, doctype))}"
						download="${esc(file.file_name)}">${esc(__("Download"))}</a>
					${
						can_file
							? `<button class="btn btn-xs btn-default" data-file-index="${file.index}">${esc(__("File as document"))}</button>`
							: ""
					}
				</td>
			</tr>`,
		)
		.join("");
	return `<h5>${esc(__("Attachments"))}</h5>
		<table class="table table-condensed docarchive-table"><tbody>${rows}</tbody></table>`;
}

function file_attachment(name, source, file) {
	fetch(attachment_url(name, file.index), { credentials: "same-origin" })
		.then((response) => {
			if (!response.ok) throw new Error(response.statusText);
			return response.blob();
		})
		.then((blob) =>
			docarchive.upload_documents({
				counterparty_type: source.counterparty_type,
				counterparty: source.counterparty,
				files: [new File([blob], core.safe_file_name(file.file_name), { type: file.content_type })],
				defaults: {
					source_doctype: DOCUMENT,
					source_name: name,
					document_date: source.document_date,
				},
			}),
		)
		.catch(() => frappe.msgprint(__("The attachment could not be loaded.")));
}

docarchive.mail_element = function (name) {
	return frappe.xcall(`${VIEWER}.render`, { name }).then((mail) => {
		const $element = $(`<div class="docarchive-mail-preview">${headers_html(mail)}${
			mail.html
				? `<iframe class="docarchive-mail-body" sandbox="" referrerpolicy="no-referrer"
					title="${esc(__("E-mail"))}"></iframe>`
				: `<pre class="docarchive-mail-text">${esc(mail.text || "")}</pre>`
		}</div>`);
		if (mail.html) $element.find("iframe")[0].srcdoc = core.mail_document(mail.html, false);
		return $element[0];
	});
};

docarchive.set_archive_headline = function (frm, state) {
	const { html, color } = core.archive_headline(state, {
		link: doc_link,
		date: docarchive.format_retain_until,
		t: __,
	});
	frm.dashboard.set_headline(html, color);
};

docarchive.show_mail = function (name, { doctype = DOCUMENT } = {}) {
	const is_document = doctype === DOCUMENT;
	Promise.all([
		frappe.xcall(`${VIEWER}.render`, { name, doctype }),
		is_document
			? frappe.db.get_value(DOCUMENT, name, ["counterparty_type", "counterparty", "document_date"])
			: Promise.resolve({ message: {} }),
	]).then(([mail, { message: source }]) => {
		const can_file = is_document && frappe.model.can_create(DOCUMENT) && source.counterparty;
		const dialog = new frappe.ui.Dialog({
			title: core.mail_title(mail.headers.subject, __("(no subject)")),
			size: "extra-large",
			fields: [{ fieldname: "mail", fieldtype: "HTML" }],
		});
		const $mail = dialog.fields_dict.mail.$wrapper;
		const body = mail.html
			? `<iframe class="docarchive-mail-body" sandbox="allow-popups allow-popups-to-escape-sandbox"
				referrerpolicy="no-referrer" title="${esc(__("E-mail"))}"></iframe>`
			: `<pre class="docarchive-mail-text">${esc(mail.text || "")}</pre>`;
		const images = mail.remote_images
			? `<p class="text-muted small">${esc(__("Remote images are blocked."))}
				<button class="btn btn-xs btn-default" data-action="load-images">${esc(__("Load images"))}</button></p>`
			: "";
		$mail.html(`${headers_html(mail)}${images}${body}${attachments_html(name, mail, can_file, doctype)}`);
		const show = (html, remote) => {
			$mail.find("iframe")[0].srcdoc = core.mail_document(html, remote);
		};
		if (mail.html) show(mail.html, false);
		$mail.on("click", "[data-action=load-images]", (event) => {
			show(load_remote_images(mail.html), true);
			$(event.currentTarget).closest("p").remove();
		});
		$mail.on("click", "[data-file-index]", (event) => {
			const index = Number(event.currentTarget.dataset.fileIndex);
			dialog.hide();
			file_attachment(name, source, mail.attachments[index]);
		});
		dialog.show();
	});
};

function gap_state(item) {
	const label = esc(GAP_STATES()[item.state] || item.state);
	if (!item.business_document) return label;
	return `<a href="${esc(frappe.utils.get_form_link(DOCUMENT, item.business_document))}">${label}</a>`;
}

function thread_row(item) {
	if (item.kind === "mail") {
		const direction = item.direction ? __(item.direction) : "";
		const show =
			item.channel === "E-mail"
				? `<button class="btn btn-xs btn-default" data-show-mail="${esc(item.name)}">${esc(__("Show e-mail"))}</button>`
				: "";
		return `<tr>
			<td class="docarchive-nowrap">${esc(item.at ? frappe.datetime.str_to_user(item.at) : "")}</td>
			<td class="docarchive-nowrap">${esc(direction)}</td>
			<td>${esc(item.sender)}</td>
			<td><a href="${esc(frappe.utils.get_form_link(DOCUMENT, item.name))}">${esc(item.title || item.name)}</a></td>
			<td class="docarchive-actions">${show}</td>
		</tr>`;
	}
	const can_create = frappe.model.can_create(DOCUMENT);
	const archive =
		can_create && item.state === "captured" && item.communication
			? `<button class="btn btn-xs btn-default" data-archive="${esc(item.communication)}">${esc(__("Archive"))}</button>`
			: "";
	return `<tr class="docarchive-gap">
		<td colspan="2"><span class="indicator-pill orange">${esc(__("Gap"))}</span></td>
		<td>${gap_state(item)}</td>
		<td class="text-muted">${esc(item.message_id)}</td>
		<td class="docarchive-actions">
			${archive}
			${
				can_create && item.state !== "archived"
					? `<button class="btn btn-xs btn-default" data-upload-gap="${esc(item.message_id)}">${esc(__("Upload .eml"))}</button>`
					: ""
			}
		</td>
	</tr>`;
}

function thread_html(items) {
	if (!items.length) return `<p class="text-muted">${esc(__("This document has no thread."))}</p>`;
	const headers = [__("Date"), __("Direction"), __("Sender or recipient"), __("Subject"), ""];
	return `<table class="table table-condensed docarchive-table">
		<thead><tr>${headers.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead>
		<tbody>${items.map(thread_row).join("")}</tbody>
	</table>`;
}

docarchive.show_thread = function (name) {
	const dialog = new frappe.ui.Dialog({
		title: __("Thread"),
		size: "extra-large",
		fields: [{ fieldname: "thread", fieldtype: "HTML" }],
	});
	const $thread = dialog.fields_dict.thread.$wrapper;
	const load = () =>
		frappe.xcall("docarchive.mail.thread.thread", { name }).then((items) => {
			$thread.html(thread_html(items));
		});
	$thread.on("click", "[data-show-mail]", (event) =>
		docarchive.show_mail(event.currentTarget.dataset.showMail),
	);
	$thread.on("click", "[data-archive]", (event) =>
		docarchive.archive_communications([event.currentTarget.dataset.archive], load),
	);
	$thread.on("click", "[data-upload-gap]", (event) =>
		docarchive.upload_eml({ gap_message_id: event.currentTarget.dataset.uploadGap, on_saved: load }),
	);
	load().then(() => dialog.show());
};

function report(lines, title) {
	frappe.msgprint({ title, message: lines.join("<br>"), indicator: "blue" });
}

function warning_text(names, { automated, internal }) {
	if (names.length === 1) {
		const single = automated.length
			? __("This e-mail looks automated, e.g. a newsletter or an automatic reply.")
			: __("This e-mail only has internal addresses.");
		return `${single} ${__("Archive it anyway?")}`;
	}
	return __(
		"Of {0} selected e-mails, {1} look automated and {2} only have internal addresses. Archive them anyway?",
		[names.length, automated.length, internal.length],
	);
}

function archive_with_warnings(names, on_done, methods) {
	return frappe.xcall(methods.warnings, { names }).then((found) => {
		if (!found.automated.length && !found.internal.length) return archive_mails(names, on_done, methods);
		return new Promise((resolve) =>
			frappe.confirm(warning_text(names, found), () =>
				resolve(archive_mails(names, on_done, methods)),
			),
		);
	});
}

docarchive.archive_communications = function (names, on_done) {
	return archive_with_warnings(names, on_done, {
		warnings: `${ARCHIVING}.warnings`,
		archive: `${ARCHIVING}.archive_communications`,
	});
};

docarchive.archive_originals = function (names, on_done) {
	return archive_with_warnings(names, on_done, {
		warnings: `${INBOX}.warnings`,
		archive: `${INBOX}.archive_originals`,
	});
};

function archive_mails(names, on_done, methods) {
	return frappe
		.call({ method: methods.archive, args: { names }, freeze: true })
		.then(({ message: result }) => {
			const archived = Object.entries(result.archived);
			const refused = Object.entries(result.refused);
			if (archived.length === 1 && !refused.length) {
				frappe.show_alert({
					message: __("Filed as {0}", [doc_link(archived[0][1])]),
					indicator: "green",
				});
			} else {
				report(
					core.archive_lines(result, {
						link: doc_link,
						archived_as: (link) => __("Filed as {0}", [link]),
					}),
					__("Archive e-mails"),
				);
			}
			on_done && on_done(result);
			return result;
		});
}

docarchive.discard_documents = function (names, on_done) {
	frappe.confirm(
		__(
			"Discard {0} document(s) and their captured e-mails? This cannot be undone. Archived documents are kept.",
			[names.length],
		),
		() =>
			frappe
				.call({ method: `${ARCHIVING}.discard`, args: { names }, freeze: true })
				.then(({ message: result }) => {
					const refused = Object.entries(result.refused);
					if (refused.length) {
						report(
							[
								__("{0} document(s) discarded.", [result.discarded.length]),
								...refused.map(([name, reason]) => `${esc(name)}: ${esc(reason)}`),
							],
							__("Discard"),
						);
					} else {
						frappe.show_alert({
							message: __("{0} document(s) discarded.", [result.discarded.length]),
							indicator: "green",
						});
					}
					on_done && on_done(result);
				}),
	);
};

function ask_direction(count, on_direction, on_cancel) {
	let chosen = false;
	const dialog = new frappe.ui.Dialog({
		title: __("Upload e-mails"),
		fields: [
			{
				fieldname: "info",
				fieldtype: "HTML",
				options: `<p class="text-muted">${esc(__("{0} e-mail file(s) will be archived as correspondence.", [count]))}</p>`,
			},
			{
				fieldname: "direction",
				fieldtype: "Select",
				label: __("Direction"),
				options: [
					{ value: "Incoming", label: __("Incoming") },
					{ value: "Outgoing", label: __("Outgoing") },
				],
				reqd: 1,
			},
		],
		primary_action_label: __("Archive"),
		primary_action({ direction }) {
			chosen = true;
			dialog.hide();
			on_direction(direction);
		},
	});
	dialog.onhide = () => chosen || on_cancel();
	dialog.show();
}

docarchive.archive_emls = function (
	files,
	{ counterparty_type, counterparty, gap_message_id, source_doctype, source_name, on_saved },
) {
	const file_urls = files.map((file) => file.file_url);
	const discard = () => frappe.xcall("docarchive.documents.discard_uploads", { file_urls });
	ask_direction(
		files.length,
		async (direction) => {
			const names = [];
			for (const file_url of file_urls) {
				try {
					names.push(
						await frappe.xcall(`${ARCHIVING}.archive_upload`, {
							file_url,
							direction,
							gap_message_id,
							counterparty_type,
							counterparty,
							source_doctype,
							source_name,
						}),
					);
				} catch {
					// the server already showed why
				}
			}
			await discard();
			if (names.length) {
				frappe.show_alert({
					message: __("Filed as {0}", [names.map(doc_link).join(", ")]),
					indicator: "green",
				});
			}
			on_saved && on_saved(names);
		},
		() => discard().then(() => on_saved && on_saved([])),
	);
};

docarchive.upload_eml = function ({ gap_message_id, on_saved }) {
	const uploaded = [];
	const uploader = new frappe.ui.FileUploader({
		allow_multiple: false,
		disable_file_browser: true,
		make_attachments_public: false,
		allow_toggle_private: false,
		restrictions: { allowed_file_types: [".eml"] },
		on_success: (file) => uploaded.push({ file_url: file.file_url, file_name: file.file_name }),
	});
	uploader.dialog.onhide = () => {
		if (uploaded.length) docarchive.archive_emls(uploaded, { gap_message_id, on_saved });
	};
};
