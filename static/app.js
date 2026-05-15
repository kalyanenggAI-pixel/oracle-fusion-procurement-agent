const sessionId = document.body.dataset.sessionId;

const selectedPdf = document.getElementById("selected-pdf");
const eventLog = document.getElementById("event-log");
const quoteSummary = document.getElementById("quote-summary");
const resolvedSummary = document.getElementById("resolved-summary");
const quoteTableContainer = document.getElementById("quote-table-container");
const resolvedTableContainer = document.getElementById("resolved-table-container");
const resultContainer = document.getElementById("result-container");
const pdfPages = document.getElementById("pdf-pages");
const commandPanel = document.querySelector(".command-panel");
const createButton = document.getElementById("create-btn");

function apiUrl(path) {
    return `${path}?session_id=${encodeURIComponent(sessionId)}`;
}

function money(currency, amount) {
    const value = Number(amount || 0);
    return `${currency || "USD"} ${value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function showError(message) {
    const existing = commandPanel.querySelector(".toast.error");
    if (existing) {
        existing.remove();
    }
    const toast = document.createElement("div");
    toast.className = "toast error";
    toast.textContent = message;
    commandPanel.appendChild(toast);
    window.setTimeout(() => toast.remove(), 6500);
}

function renderEvents(events) {
    eventLog.innerHTML = "";
    if (!events || !events.length) {
        const item = document.createElement("li");
        item.textContent = "No actions yet. Start by selecting a PDF.";
        eventLog.appendChild(item);
        return;
    }
    [...events].reverse().forEach((entry) => {
        const item = document.createElement("li");
        item.textContent = entry;
        eventLog.appendChild(item);
    });
}

function renderSummary(target, chips) {
    target.innerHTML = "";
    chips.forEach((chip) => {
        const span = document.createElement("span");
        span.className = "summary-chip";
        span.textContent = chip;
        target.appendChild(span);
    });
}

function renderTable(headers, rows) {
    const table = document.createElement("table");
    const thead = document.createElement("thead");
    const tbody = document.createElement("tbody");
    const headRow = document.createElement("tr");
    headers.forEach((header) => {
        const th = document.createElement("th");
        th.textContent = header;
        headRow.appendChild(th);
    });
    thead.appendChild(headRow);
    rows.forEach((row) => {
        const tr = document.createElement("tr");
        row.forEach((cell) => {
            const td = document.createElement("td");
            td.textContent = cell;
            tr.appendChild(td);
        });
        tbody.appendChild(tr);
    });
    table.appendChild(thead);
    table.appendChild(tbody);
    return table;
}

function renderMetrics(result) {
    const wrapper = document.createElement("div");
    wrapper.className = "metric-grid";
    const metrics = [
        ["Requisition Number", result.requisition_number],
        ["Requisition ID", result.requisition_id],
        ["Status", result.status],
        ["Lines Created", String(result.lines_created)],
        ["Total Amount", money(result.currency, result.total_amount)],
    ];
    metrics.forEach(([label, value]) => {
        const card = document.createElement("div");
        card.className = "metric-card";
        const title = document.createElement("span");
        title.className = "metric-label";
        title.textContent = label;
        const content = document.createElement("div");
        content.className = "metric-value";
        content.textContent = value;
        card.appendChild(title);
        card.appendChild(content);
        wrapper.appendChild(card);
    });
    return wrapper;
}

function renderPdfPages(pageUrls) {
    if (!pageUrls || !pageUrls.length) {
        pdfPages.className = "pdf-pages empty-state";
        pdfPages.textContent = "Select a PDF to render it inside this panel.";
        return;
    }

    pdfPages.className = "pdf-pages";
    pdfPages.innerHTML = "";
    pageUrls.forEach((url, index) => {
        const image = document.createElement("img");
        image.className = "pdf-page";
        image.alt = `PDF page ${index + 1}`;
        image.src = `${url}&t=${Date.now()}`;
        pdfPages.appendChild(image);
    });
}

function renderState(state) {
    createButton.textContent = state.dry_run
        ? createButton.dataset.dryRunLabel
        : createButton.dataset.liveLabel;

    selectedPdf.textContent = state.pdf_selected
        ? `Selected PDF: ${state.pdf_name}`
        : "No PDF selected yet.";

    renderPdfPages(state.pdf_pages || []);
    renderEvents(state.events || []);

    if (state.quote) {
        quoteTableContainer.className = "table-shell";
        renderSummary(quoteSummary, [
            `Supplier: ${state.quote.supplier_name}`,
            `Quote Date: ${state.quote.quote_date}`,
            `Currency: ${state.quote.currency}`,
            `Lines: ${state.quote.lines.length}`,
        ]);
        quoteTableContainer.innerHTML = "";
        const rows = state.quote.lines.map((line) => [
            String(line.line_number),
            line.item_description,
            String(line.quantity),
            money(line.currency, line.unit_price),
            line.unit_of_measure,
            line.category_hint || "-",
        ]);
        quoteTableContainer.appendChild(
            renderTable(["Line", "Item", "Qty", "Unit Price", "UOM", "Category Hint"], rows)
        );
    } else {
        quoteSummary.innerHTML = "";
        quoteTableContainer.className = "table-shell empty-state";
        quoteTableContainer.textContent = "Extract a PDF to see line items here.";
    }

    if (state.preview && state.resolved_payload) {
        renderSummary(resolvedSummary, [
            `Business Unit: ${state.preview.business_unit_name}`,
            `Requester: ${state.preview.requester_email}`,
            `Total: ${money(state.preview.currency, state.preview.total_amount)}`,
            `Lines: ${state.preview.lines.length}`,
        ]);
        resolvedTableContainer.className = "table-shell";
        resolvedTableContainer.innerHTML = "";
        const rows = state.preview.lines.map((line) => [
            String(line.line_number),
            line.item_description,
            line.category_name,
            line.uom_code,
            line.need_by_date,
            money(line.currency, line.unit_price),
        ]);
        resolvedTableContainer.appendChild(
            renderTable(["Line", "Item", "Oracle Category", "UOM", "Need By", "Unit Price"], rows)
        );
    } else {
        resolvedSummary.innerHTML = "";
        resolvedTableContainer.className = "table-shell empty-state";
        resolvedTableContainer.textContent = "Resolve the extracted lines to see Oracle categories and UOM codes.";
    }

    if (state.requisition_result) {
        resultContainer.className = "result-shell";
        resultContainer.innerHTML = "";
        if (state.dry_run) {
            const note = document.createElement("div");
            note.className = "summary-chip";
            note.textContent = "Dry run only. Nothing was sent to Oracle Fusion.";
            resultContainer.appendChild(note);
        }
        resultContainer.appendChild(renderMetrics(state.requisition_result));
    } else {
        resultContainer.className = "result-shell empty-state";
        resultContainer.textContent = "Create the requisition to see the final result here.";
    }
}

async function callJson(path, options = {}) {
    const response = await fetch(apiUrl(path), options);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
        throw new Error(payload.detail || "Request failed.");
    }
    return payload;
}

async function refreshState() {
    const state = await callJson("/api/state");
    renderState(state);
}

document.getElementById("sample-btn").addEventListener("click", async () => {
    try {
        renderState(await callJson("/api/use-sample", { method: "POST" }));
    } catch (error) {
        showError(error.message);
    }
});

document.getElementById("file-input").addEventListener("change", async (event) => {
    const [file] = event.target.files;
    if (!file) {
        return;
    }
    const formData = new FormData();
    formData.append("file", file);
    try {
        renderState(
            await callJson("/api/upload", {
                method: "POST",
                body: formData,
            })
        );
    } catch (error) {
        showError(error.message);
    } finally {
        event.target.value = "";
    }
});

document.getElementById("prepare-btn").addEventListener("click", async () => {
    try {
        renderState(await callJson("/api/prepare", { method: "POST" }));
    } catch (error) {
        showError(error.message);
    }
});

document.getElementById("create-btn").addEventListener("click", async () => {
    try {
        renderState(await callJson("/api/create", { method: "POST" }));
    } catch (error) {
        showError(error.message);
    }
});

document.getElementById("reset-btn").addEventListener("click", async () => {
    try {
        renderState(await callJson("/api/reset", { method: "POST" }));
    } catch (error) {
        showError(error.message);
    }
});

refreshState().catch((error) => showError(error.message));
