const ARCHIVE_COLORS = { Pending: "orange", Archived: "green", Failed: "red" };
const esc = (value) => frappe.utils.escape_html(value ?? "");

function party_text(party) {
	if (!party) return "";
	const address = party.address || {};
	const place = [address.postcode, address.city].filter(Boolean).join(" ");
	const ids = [...(party.vat_ids || []), ...(party.tax_numbers || [])];
	return [party.name, address.line1, place, address.country, ids.join(", ")].filter(Boolean).join(" · ");
}

function einvoice_html(data) {
	const totals = data.totals || {};
	const money = (value) => (value ? `${value} ${data.currency || ""}` : "");
	const rows = [
		[__("Type"), data.kind === "credit_note" ? __("Credit Note") : __("Invoice")],
		[__("Format"), [data.syntax, data.profile].filter(Boolean).join(" · ")],
		[__("Invoice Number"), data.number],
		[__("Issue Date"), data.issue_date && frappe.datetime.str_to_user(data.issue_date)],
		[__("Due Date"), data.due_date && frappe.datetime.str_to_user(data.due_date)],
		[__("Seller"), party_text(data.seller)],
		[__("Buyer"), party_text(data.buyer)],
		[__("Buyer Reference"), data.buyer_reference],
		[__("Net"), money(totals.net)],
		[__("Tax"), money(totals.tax)],
		[__("Gross"), money(totals.gross)],
		[__("Read From"), data.embedded_file ? `${data.file_name} › ${data.embedded_file}` : data.file_name],
	].filter(([, value]) => value);
	const conflicts = (data.conflicts || []).map(
		(c) => {
			const value = c.field === "document_date" ? frappe.datetime.str_to_user(c.xml) : c.xml;
			return `<p class="text-warning">${esc(__("{0} in the XML differs: {1}", [__(c.label), value]))}</p>`;
		},
	);
	return `${conflicts.join("")}<table class="table table-condensed table-borderless">${rows
		.map(([label, value]) => `<tr><td class="text-muted">${esc(label)}</td><td>${esc(value)}</td></tr>`)
		.join("")}</table>`;
}

frappe.ui.form.on("Business Document", {
	setup(frm) {
		frappe.xcall("docarchive.counterparty.doctypes").then((doctypes) => {
			frm.set_query("counterparty_type", () => ({
				filters: doctypes.length
					? { name: ["in", doctypes] }
					: { istable: 0, issingle: 0 },
			}));
		});
	},
	refresh(frm) {
		const data = frm.doc.einvoice_data && JSON.parse(frm.doc.einvoice_data);
		frm.get_field("einvoice_html").$wrapper.html(data ? einvoice_html(data) : "");
		if (frm.is_new()) return;
		const status = frm.doc.archive_status || "Pending";
		frm.page.set_indicator(__(status), ARCHIVE_COLORS[status]);
		if (!frm.doc.archive_started_at && frm.perm[0].write && !frm.is_dirty()) {
			frappe
				.xcall("docarchive.archive_now.preview", { names: [frm.doc.name] })
				.then(([row]) => {
					if (!row.eligible || frm.doc.archive_started_at) return;
					frm.add_custom_button(__("Archive now"), () =>
						docarchive.confirm_archive([row], () => frm.reload_doc()),
					);
				});
		}
		if (frm.doc.archive_status === "Archived") {
			docarchive.extension_options(frm.doc.name).then((options) => {
				if (options.pending) {
					frm.set_intro(
						__("The retention is being extended to {0}.", [
							docarchive.format_retain_until(options.pending),
						]),
						"orange",
					);
				}
				if (!options.allowed) return;
				frm.add_custom_button(__("Extend retention"), () =>
					docarchive.extend_retention(frm.doc.name, () => frm.reload_doc()),
				);
			});
		}
		if (!frm.docarchive_listening) {
			frm.docarchive_listening = true;
			frappe.realtime.on("docarchive_archive_now", (data) => {
				if (data.name !== frm.doc.name) return;
				frappe.show_alert({
					message: __("Archive status: {0}", [__(data.status)]),
					indicator: ARCHIVE_COLORS[data.status],
				});
				frm.reload_doc();
			});
		}
	},
	counterparty_type(frm) {
		frm.set_value("counterparty", null);
	},
});
