
let conversationId = null;
let currentDomain = "general";

// Sélectionner les éléments HTML
const form = document.querySelector("#prompt-form");
const input = document.querySelector("#prompt");
const output = document.querySelector("#gpt-output");
const domainSelect = document.querySelector("#domain");
const newChatButton = document.querySelector("#new-chat");
const exportSelect = document.querySelector("#export-format");
const exportButton = document.querySelector("#export-button");
const statsButton = document.querySelector("#stats-button");
const statsOutput = document.querySelector("#stats-output");

// Afficher un message dans la conversation
function addMessage(role, content, messageId = null) {
    const block = document.createElement("div");
    block.className = "message rounded-xl border p-4 mb-3";

    const title = document.createElement("strong");
    title.textContent = role === "user" ? "Vous" : "Assistant";

    const text = document.createElement("p");
    text.className = "whitespace-pre-wrap mt-2";
    text.textContent = content;

    block.appendChild(title);
    block.appendChild(text);

    // Ajouter une note pour les réponses de l'assistant
    if (role === "assistant" && messageId !== null) {
        const rating = document.createElement("div");
        rating.className = "mt-3";

        const label = document.createElement("span");
        label.textContent = "Évaluer la réponse : ";

        rating.appendChild(label);

        for (let i = 1; i <= 5; i++) {
            const star = document.createElement("button");
            star.type = "button";
            star.textContent = "★";
            star.className = "px-1 text-yellow-500";
            star.title = `Note ${i}/5`;

            star.addEventListener("click", async () => {
                try {
                    const response = await fetch(
                        `/messages/${messageId}/rating`,
                        {
                            method: "POST",
                            headers: {
                                "Content-Type": "application/json"
                            },
                            body: JSON.stringify({ rating: i })
                        }
                    );

                    const data = await response.json();

                    if (!response.ok) {
                        throw new Error(data.error || "Erreur de notation");
                    }

                    label.textContent = `Votre note : ${i}/5 `;
                } catch (error) {
                    alert(error.message);
                }
            });

            rating.appendChild(star);
        }

        block.appendChild(rating);
    }

    output.appendChild(block);
    return block;
}


// Créer une nouvelle conversation
async function createConversation() {
    try {
        currentDomain = domainSelect.value;

        const response = await fetch("/conversations", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                title: "Nouvelle conversation",
                domain: currentDomain
            })
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || "Impossible de créer la conversation");
        }

        conversationId = data.id;
        output.innerHTML = "";
        statsOutput.textContent = "";

        addMessage("assistant", "Bonjour ! Comment puis-je vous aider ?");

    } catch (error) {
        alert(error.message);
    }
}


// Envoyer un message à l'assistant
form.addEventListener("submit", async (event) => {
    event.preventDefault();

    const message = input.value.trim();

    if (!message) return;

    // Créer une conversation si nécessaire
    if (conversationId === null) {
        await createConversation();

        if (conversationId === null) return;
    }

    addMessage("user", message);
    input.value = "";

    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;

    const loading = addMessage("assistant", "Réponse en cours...");

    try {
        const response = await fetch("/prompt", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                conversation_id: conversationId,
                message: message,
                domain: domainSelect.value
            })
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || "Une erreur est survenue");
        }

        // Remplacer le message de chargement par la réponse
        loading.querySelector("p").textContent = data.answer;

        // Afficher le temps de réponse et les tokens
        const info = document.createElement("small");
        info.className = "block mt-2 text-gray-500";
        info.textContent =
            `Temps : ${data.duration} s | Tokens : ${data.tokens}`;

        loading.appendChild(info);

        // Récupérer l'identifiant de la réponse sauvegardée
        await loadConversation();

    } catch (error) {
        loading.querySelector("p").textContent = error.message;
    } finally {
        button.disabled = false;
        input.focus();
    }
});


// Charger l'historique depuis SQLite
async function loadConversation() {
    if (conversationId === null) return;

    try {
        const response = await fetch(`/conversations/${conversationId}`);
        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || "Erreur de chargement");
        }

        // La dernière réponse de l'assistant est sauvegardée en base.
        // On conserve ici l'affichage actuel pour éviter les doublons.
        return data.messages;

    } catch (error) {
        console.error(error);
    }
}


// Bouton nouvelle conversation
newChatButton.addEventListener("click", async () => {
    conversationId = null;
    output.innerHTML = "";
    statsOutput.textContent = "";
    await createConversation();
});


// Changer de domaine
domainSelect.addEventListener("change", () => {
    currentDomain = domainSelect.value;
});


// Exporter la conversation
exportButton.addEventListener("click", () => {
    if (conversationId === null) {
        alert("Créez d'abord une conversation.");
        return;
    }

    const format = exportSelect.value;

    window.location.href =
        `/conversations/${conversationId}/export/${format}`;
});


// Afficher les statistiques
statsButton.addEventListener("click", async () => {
    if (conversationId === null) {
        alert("Aucune conversation à analyser.");
        return;
    }

    try {
        const response = await fetch(`/stats/${conversationId}`);
        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || "Erreur de statistiques");
        }

        statsOutput.textContent =
            `Nombre de réponses : ${data.total_messages} | ` +
            `Tokens : ${data.total_tokens} | ` +
            `Temps moyen : ${data.average_duration} s | ` +
            `Note moyenne : ${data.average_rating}/5`;

    } catch (error) {
        statsOutput.textContent = error.message;
    }
});
