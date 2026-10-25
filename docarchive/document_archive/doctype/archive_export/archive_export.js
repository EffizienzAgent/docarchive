frappe.ui.form.on("Archive Export", {
	refresh(frm) {
		const colors = {
			Queued: "orange",
			Running: "blue",
			Completed: "green",
			Failed: "red",
			Expired: "gray",
		};
		frm.page.set_indicator(__(frm.doc.status), colors[frm.doc.status]);
		if (frm.doc.status === "Running") {
			frm.dashboard.show_progress(
				__("Export"),
				frm.doc.progress || 0,
				__("{0} of {1} documents", [frm.doc.exported_documents || 0, frm.doc.total_documents || 0]),
			);
		}
		if (frm.doc.status === "Completed") {
			for (const part of frm.doc.parts || []) {
				if (!part.file) continue;
				const label =
					frm.doc.parts.length > 1
						? __("Download part {0}", [part.part])
						: __("Download");
				frm.add_custom_button(label, () => window.open(part.file));
			}
			if (frm.doc.mismatches) {
				frm.dashboard.set_headline(
					__("{0} objects could not be verified. See Mismatch Details.", [frm.doc.mismatches]),
					"red",
				);
			}
		}
		if (!frm.docarchive_export_listening) {
			frm.docarchive_export_listening = true;
			frappe.realtime.on("docarchive_export", (data) => {
				if (data.name !== frm.doc.name) return;
				if (data.status === "Running") {
					frm.dashboard.show_progress(
						__("Export"),
						data.total ? (data.done * 100) / data.total : 0,
						__("{0} of {1} documents", [data.done, data.total]),
					);
				} else {
					frm.reload_doc();
				}
			});
		}
	},
});
