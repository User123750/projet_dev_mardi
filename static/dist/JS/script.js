let conversationId = null;

const $ = (s) => document.querySelector(s);
const output = $("#gpt-output"), form = $("#prompt-form"), input = $("#prompt");
const domainSelect = $("#domain"), historySelect = $("#history"), errorBox = $("#error-box");

const el = (tag, cls = "", text = "") => {
    const e = document.createElement(tag);
    e.className = cls;
    e.textContent = text;
    return e;
};
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));

function showError(msg) {
    errorBox.textContent = msg;
    errorBox.classList.toggle("hidden", !msg);
}

// Appel API avec messages d'erreur clairs
async function api(url, opts = {}) {
    let r;
    try {
        r = await fetch(url, opts);
    } catch {
        throw new Error("Impossible de joindre le serveur. Vérifie que l'application est lancée.");
    }
    const isJson = (r.headers.get("content-type") || "").includes("json");
    if (!r.ok) {
        const d = isJson ? await r.json() : {};
        throw new Error(d.error || "Une erreur est survenue.");
    }
    return isJson ? r.json() : r.blob();
}
const post = (url, body) => api(url, {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)
});

function download(blob, name) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = name;
    a.click();
    URL.revokeObjectURL(a.href);
}

// Code couleur : vert (4-5), orange (3), rouge (1-2), gris (non noté)
function colorClass(r) {
    if (r >= 4) return "border-green-500 bg-green-50";
    if (r === 3) return "border-orange-400 bg-orange-50";
    if (r >= 1) return "border-red-500 bg-red-50";
    return "border-gray-200 bg-white";
}

function renderSources(sources) {
    const box = el("div", "mt-3 text-sm");
    box.appendChild(el("p", "font-semibold", "Sources utilisées :"));
    const lines = $("#lines").value;
    sources.forEach((s) => {
        const item = el("div", "border-l-4 border-indigo-300 pl-2 my-2");
        item.appendChild(el("p", "text-indigo-700", `[${s.tag}] ${s.document} - passage ${s.passage}`));
        const t = el("p", "text-gray-600 whitespace-pre-wrap cursor-pointer", s.text);
        Object.assign(t.style, {display: "-webkit-box", WebkitBoxOrient: "vertical",
            WebkitLineClamp: lines, overflow: "hidden"});
        t.title = "Cliquer pour afficher tout le passage";
        t.addEventListener("click", () => { t.style.WebkitLineClamp = t.style.WebkitLineClamp === "none" ? lines : "none"; });
        item.appendChild(t);
        box.appendChild(item);
    });
    return box;
}

function addMessage(m) {
    const isUser = m.role === "user";
    const block = el("div", `rounded-xl border-2 p-4 mb-3 ${isUser ? "border-gray-200 bg-white" : colorClass(m.rating || 0)}`);
    block.appendChild(el("strong", "", isUser ? "Vous" : "Assistant"));
    const text = el("p", "whitespace-pre-wrap mt-2", m.content);
    block.appendChild(text);

    if (!isUser && m.id) {
        if (m.duration !== undefined && m.duration !== null) {
            block.appendChild(el("small", "block mt-2 text-gray-500",
                `Temps : ${m.duration} s | Tokens : ${m.tokens || 0}`));
        }
        if (m.sources && m.sources.length) block.appendChild(renderSources(m.sources));

        const rating = el("div", "mt-3");
        const label = el("span", "text-sm", m.rating ? `Votre note : ${m.rating}/5 ` : "Évaluer : ");
        rating.appendChild(label);
        for (let i = 1; i <= 5; i++) {
            const star = el("button", "px-1 text-yellow-500 text-lg", "★");
            star.type = "button";
            star.title = `Note ${i}/5`;
            star.addEventListener("click", async () => {
                try {
                    await post(`/messages/${m.id}/rating`, {rating: i});
                    label.textContent = `Votre note : ${i}/5 `;
                    block.className = `rounded-xl border-2 p-4 mb-3 ${colorClass(i)}`;
                } catch (e) { showError(e.message); }
            });
            rating.appendChild(star);
        }
        block.appendChild(rating);
    }
    output.appendChild(block);
    block.scrollIntoView({behavior: "smooth", block: "nearest"});
    return block;
}

function resetChat() {
    conversationId = null;
    output.innerHTML = "";
    $("#stats-output").textContent = "Aucune statistique pour le moment.";
    showError("");
    addMessage({role: "assistant", content: "Bonjour ! Comment puis-je vous aider ?"});
}

async function loadHistory() {
    try {
        const list = await api("/conversations");
        historySelect.innerHTML = '<option value="">Conversations précédentes…</option>';
        list.forEach((c) => {
            const o = el("option", "", `${c.title} (${c.domain})`);
            o.value = c.id;
            historySelect.appendChild(o);
        });
    } catch (e) { console.error(e); }
}

async function openConversation(id) {
    try {
        const d = await api(`/conversations/${id}`);
        conversationId = d.conversation.id;
        domainSelect.value = d.conversation.domain || "general";
        output.innerHTML = "";
        d.messages.forEach(addMessage);
        showError("");
        loadStats();
    } catch (e) { showError(e.message); }
}

function selectedSources() {
    return [...document.querySelectorAll(".doc-check:checked")].map((c) => Number(c.value));
}

form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const message = input.value.trim();
    if (!message) return;
    showError("");
    const button = form.querySelector("button[type=submit]");
    button.disabled = true;
    try {
        if (conversationId === null) {
            const c = await post("/conversations", {title: "Nouvelle conversation", domain: domainSelect.value});
            conversationId = c.id;
        }
        addMessage({role: "user", content: message});
        input.value = "";
        const loading = el("p", "text-gray-400 mb-3", "Réponse en cours...");
        output.appendChild(loading);
        try {
            const d = await post("/prompt", {
                conversation_id: conversationId, message, domain: domainSelect.value,
                source_ids: selectedSources(), top_k: Number($("#top-k").value)
            });
            loading.remove();
            addMessage({role: "assistant", content: d.answer, id: d.id, duration: d.duration,
                tokens: d.tokens, sources: d.sources, rating: 0});
            loadHistory();
            loadStats();
        } catch (e) {
            loading.remove();
            showError(e.message);
        }
    } catch (e) {
        showError(e.message);
    } finally {
        button.disabled = false;
        input.focus();
    }
});

$("#new-chat").addEventListener("click", resetChat);

$("#delete-chat").addEventListener("click", async () => {
    if (conversationId !== null) {
        if (!confirm("Effacer définitivement cette conversation ?")) return;
        try { await api(`/conversations/${conversationId}`, {method: "DELETE"}); }
        catch (e) { showError(e.message); return; }
    }
    resetChat();
    loadHistory();
});

historySelect.addEventListener("change", () => { if (historySelect.value) openConversation(historySelect.value); });

// Export et résumé
$("#export-button").addEventListener("click", async () => {
    if (conversationId === null) return showError("Aucune conversation à exporter.");
    const f = $("#export-format").value;
    try { download(await api(`/conversations/${conversationId}/export/${f}`), `conversation.${f}`); }
    catch (e) { showError(e.message); }
});

$("#summary-button").addEventListener("click", async () => {
    if (conversationId === null) return showError("Aucune conversation à résumer.");
    const b = $("#summary-button");
    b.disabled = true;
    b.textContent = "Résumé en cours...";
    try { download(await api(`/conversations/${conversationId}/summary`), "resume.pdf"); }
    catch (e) { showError(e.message); }
    finally { b.disabled = false; b.textContent = "Résumé PDF"; }
});

// Performances
async function loadStats() {
    if (conversationId === null) return;
    try {
        const d = await api(`/stats/${conversationId}`);
        const maxT = Math.max(...d.items.map((i) => i.duration || 0), 0.01);
        const rows = d.items.map((i, n) => `<tr class="border-t">
            <td class="p-1">#${n + 1}</td>
            <td class="p-1"><div class="bg-indigo-400 h-2 rounded" style="width:${Math.round(100 * (i.duration || 0) / maxT)}%"></div>${i.duration || 0} s</td>
            <td class="p-1">${i.prompt_tokens || 0} / ${i.completion_tokens || 0} / <b>${i.tokens || 0}</b></td>
            <td class="p-1">${i.rating ? i.rating + "/5" : "-"}</td></tr>`).join("");
        $("#stats-output").innerHTML = `
            <p class="mb-2">Réponses : <b>${d.total_messages}</b> | Tokens : <b>${d.total_tokens}</b> |
            Temps moyen : <b>${d.average_duration} s</b> | Note moyenne : <b>${d.average_rating}/5</b></p>
            <table class="w-full text-left"><thead><tr><th class="p-1">Réponse</th><th class="p-1">Temps</th>
            <th class="p-1">Tokens (entrée / sortie / total)</th><th class="p-1">Note</th></tr></thead>
            <tbody>${rows}</tbody></table>`;
    } catch (e) { $("#stats-output").textContent = e.message; }
}
$("#stats-button").addEventListener("click", () => {
    if (conversationId === null) return showError("Aucune conversation à analyser.");
    loadStats();
});

// Documents (sources)
async function loadDocs() {
    try {
        const docs = await api("/documents");
        const list = $("#doc-list");
        list.innerHTML = "";
        if (!docs.length) list.textContent = "Aucun document. Importez un PDF, TXT ou MD.";
        docs.forEach((d) => {
            const row = el("div", "flex items-center gap-2 py-1");
            const cb = el("input", "doc-check");
            cb.type = "checkbox";
            cb.value = d.id;
            cb.checked = true;
            const name = el("span", "flex-1", d.name);
            const del = el("button", "text-red-600", "Supprimer");
            del.type = "button";
            del.addEventListener("click", async () => {
                try { await api(`/documents/${d.id}`, {method: "DELETE"}); loadDocs(); }
                catch (e) { showError(e.message); }
            });
            row.append(cb, name, del);
            list.appendChild(row);
        });
    } catch (e) { console.error(e); }
}

$("#doc-file").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    try { await api("/documents", {method: "POST", body: fd}); showError(""); loadDocs(); }
    catch (err) { showError(err.message); }
    e.target.value = "";
});

// Analyse de données
$("#data-file").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const out = $("#data-output");
    out.textContent = "Analyse en cours...";
    const fd = new FormData();
    fd.append("file", file);
    try {
        const d = await api("/analyze", {method: "POST", body: fd});
        const bar = (label, v) => `<div class="mb-1">${label} : <b>${v}%</b>
            <div class="bg-gray-200 rounded h-2"><div class="h-2 rounded ${v >= 90 ? "bg-green-500" : v >= 70 ? "bg-orange-400" : "bg-red-500"}" style="width:${v}%"></div></div></div>`;
        const miss = Object.entries(d.missing).filter(([, n]) => n > 0)
            .map(([c, n]) => `<li>${esc(c)} : ${n} manquante(s)</li>`).join("") || "<li>Aucune</li>";
        const issues = d.issues.map((i) => `<li>${esc(i.column)} : ${esc(i.issue)}${i.count ? " (" + i.count + ")" : ""}</li>`).join("") || "<li>Aucune</li>";
        const anom = d.anomalies.map((a) => `<li>${esc(a.column)} : ${a.count} valeur(s) atypique(s), ex. ${a.examples.map(esc).join(", ")}</li>`).join("") || "<li>Aucune</li>";
        out.innerHTML = `
            <p class="mb-2">${d.rows} lignes, ${d.columns} colonnes. Score global : <b>${d.score}/100 (${d.level})</b></p>
            ${bar("Complétude", d.completeness)}${bar("Unicité (sans doublons)", d.uniqueness)}${bar("Cohérence et validité", d.validity)}
            <p class="mt-2"><b>Doublons :</b> ${d.duplicates} ligne(s)</p>
            <p class="mt-2 font-semibold">Valeurs manquantes (${d.missing_total})</p><ul class="list-disc ml-5">${miss}</ul>
            <p class="mt-2 font-semibold">Incohérences</p><ul class="list-disc ml-5">${issues}</ul>
            <p class="mt-2 font-semibold">Anomalies (méthode IQR)</p><ul class="list-disc ml-5">${anom}</ul>`;
    } catch (err) { out.textContent = err.message; }
    e.target.value = "";
});

resetChat();
loadHistory();
loadDocs();
