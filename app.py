
import os
import sqlite3
import json
import time
from io import BytesIO
from datetime import datetime

from flask import Flask, render_template, request, jsonify, send_file
from openai import OpenAI
from dotenv import load_dotenv
from reportlab.pdfgen import canvas

# Charger les variables du fichier .env
load_dotenv()

app = Flask(__name__)
DATABASE = "flaskgpt.db"

# Connexion à Groq
client = OpenAI(
    api_key=os.getenv("GROQ_API_KEY"),
    base_url="https://api.groq.com/openai/v1"
)

MODEL = "openai/gpt-oss-20b"


# -------------------------
# 1. Base de données SQLite
# -------------------------

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            domain TEXT,
            created_at TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER,
            role TEXT,
            content TEXT,
            duration REAL,
            tokens INTEGER DEFAULT 0,
            rating INTEGER DEFAULT 0,
            created_at TEXT,
            FOREIGN KEY (conversation_id)
                REFERENCES conversations(id)
        )
    """)

    conn.commit()
    conn.close()


init_db()


# -------------------------
# 2. Page d'accueil
# -------------------------

@app.route("/")
def home():
    return render_template("index.html")


# -------------------------
# 3. Créer une conversation
# -------------------------

@app.route("/conversations", methods=["POST"])
def new_conversation():
    data = request.get_json(silent=True) or {}

    title = data.get("title", "Nouvelle conversation")
    domain = data.get("domain", "general")

    conn = get_db()
    cursor = conn.execute(
        """INSERT INTO conversations
           (title, domain, created_at)
           VALUES (?, ?, ?)""",
        (title, domain, datetime.now().isoformat())
    )

    conversation_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return jsonify({
        "id": conversation_id,
        "title": title,
        "domain": domain
    })


# -------------------------
# 4. Envoyer un message à l'IA
# -------------------------

@app.route("/prompt", methods=["POST"])
def prompt():
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({"error": "Données invalides."}), 400

    conversation_id = data.get("conversation_id")
    message = data.get("message", "").strip()
    domain = data.get("domain", "general")

    if not message:
        return jsonify({"error": "Écris une question."}), 400

    if not conversation_id:
        return jsonify({
            "error": "Crée une conversation avant d'envoyer un message."
        }), 400

    conn = get_db()

    conversation = conn.execute(
        "SELECT * FROM conversations WHERE id = ?",
        (conversation_id,)
    ).fetchone()

    if conversation is None:
        conn.close()
        return jsonify({"error": "Conversation introuvable."}), 404

    conn.execute(
        """INSERT INTO messages
           (conversation_id, role, content, created_at)
           VALUES (?, ?, ?, ?)""",
        (conversation_id, "user", message, datetime.now().isoformat())
    )
    conn.commit()

    history = conn.execute(
        """SELECT role, content FROM messages
           WHERE conversation_id = ?
           ORDER BY id""",
        (conversation_id,)
    ).fetchall()

    conn.close()

    # Adapter les réponses au domaine choisi
    domains = {
        "informatique": "Tu es un assistant spécialisé en informatique.",
        "finance": "Tu es un assistant spécialisé en finance.",
        "data": "Tu es un assistant spécialisé en analyse de données.",
        "ia": "Tu es un assistant spécialisé en intelligence artificielle.",
        "general": "Tu es un assistant utile et pédagogique."
    }

    system_message = domains.get(
        domain, domains["general"]
    )

    messages = [{"role": "system", "content": system_message}]

    for item in history:
        messages.append({
            "role": item["role"],
            "content": item["content"]
        })

    # Appel à l'API et mesure du temps
    start = time.time()

    try:
        if not os.getenv("GROQ_API_KEY"):
            return jsonify({
                "error": "La clé API Groq est absente du fichier .env."
            }), 500

        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            stream=False
        )

        answer = response.choices[0].message.content
        duration = round(time.time() - start, 2)

        tokens = 0
        if response.usage:
            tokens = response.usage.total_tokens or 0

        conn = get_db()
        conn.execute(
            """INSERT INTO messages
               (conversation_id, role, content, duration,
                tokens, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                conversation_id,
                "assistant",
                answer,
                duration,
                tokens,
                datetime.now().isoformat()
            )
        )
        conn.commit()
        conn.close()

        return jsonify({
            "answer": answer,
            "duration": duration,
            "tokens": tokens
        })

    except Exception as error:
        print("Erreur API :", error)

        error_text = str(error).lower()

        if "401" in error_text or "authentication" in error_text:
            message_error = "Clé API invalide. Vérifie ton fichier .env."
        elif "429" in error_text or "rate limit" in error_text:
            message_error = "Quota ou limite API atteint. Réessaie plus tard."
        elif "connection" in error_text or "timeout" in error_text:
            message_error = "Problème de connexion. Vérifie Internet."
        else:
            message_error = "Impossible de générer la réponse. Réessaie."

        return jsonify({"error": message_error}), 500


# -------------------------
# 5. Consulter l'historique
# -------------------------

@app.route("/conversations/<int:conversation_id>", methods=["GET"])
def get_conversation(conversation_id):
    conn = get_db()

    conversation = conn.execute(
        "SELECT * FROM conversations WHERE id = ?",
        (conversation_id,)
    ).fetchone()

    if conversation is None:
        conn.close()
        return jsonify({"error": "Conversation introuvable."}), 404

    messages = conn.execute(
        """SELECT * FROM messages
           WHERE conversation_id = ?
           ORDER BY id""",
        (conversation_id,)
    ).fetchall()

    conn.close()

    return jsonify({
        "conversation": dict(conversation),
        "messages": [dict(m) for m in messages]
    })


# -------------------------
# 6. Supprimer une conversation
# -------------------------

@app.route("/conversations/<int:conversation_id>", methods=["DELETE"])
def delete_conversation(conversation_id):
    conn = get_db()

    conn.execute(
        "DELETE FROM messages WHERE conversation_id = ?",
        (conversation_id,)
    )

    conn.execute(
        "DELETE FROM conversations WHERE id = ?",
        (conversation_id,)
    )

    conn.commit()
    conn.close()

    return jsonify({"message": "Conversation supprimée."})


# -------------------------
# 7. Évaluer une réponse
# -------------------------

@app.route("/messages/<int:message_id>/rating", methods=["POST"])
def rate_message(message_id):
    data = request.get_json(silent=True) or {}
    rating = data.get("rating")

    if not isinstance(rating, int) or not 1 <= rating <= 5:
        return jsonify({"error": "La note doit être entre 1 et 5."}), 400

    conn = get_db()
    cursor = conn.execute(
        "UPDATE messages SET rating = ? WHERE id = ? AND role = 'assistant'",
        (rating, message_id)
    )
    conn.commit()
    updated = cursor.rowcount
    conn.close()

    if not updated:
        return jsonify({"error": "Réponse introuvable."}), 404

    return jsonify({"message": "Note enregistrée."})


# -------------------------
# 8. Exporter une conversation
# -------------------------

@app.route("/conversations/<int:conversation_id>/export/<format>")
def export_conversation(conversation_id, format):
    conn = get_db()

    conversation = conn.execute(
        "SELECT * FROM conversations WHERE id = ?",
        (conversation_id,)
    ).fetchone()

    messages = conn.execute(
        """SELECT role, content, duration, tokens, rating
           FROM messages WHERE conversation_id = ?
           ORDER BY id""",
        (conversation_id,)
    ).fetchall()

    conn.close()

    if conversation is None:
        return jsonify({"error": "Conversation introuvable."}), 404

    messages = [dict(m) for m in messages]
    title = conversation["title"] or "Conversation"

    if format == "json":
        output = {
            "conversation": dict(conversation),
            "messages": messages
        }

        return app.response_class(
            json.dumps(output, ensure_ascii=False, indent=2),
            mimetype="application/json",
            headers={
                "Content-Disposition": "attachment; filename=conversation.json"
            }
        )

    text_content = "\n\n".join(
        f"{m['role'].upper()} : {m['content']}" for m in messages
    )

    if format == "txt":
        return app.response_class(
            text_content,
            mimetype="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": "attachment; filename=conversation.txt"
            }
        )

    if format == "pdf":
        buffer = BytesIO()
        pdf = canvas.Canvas(buffer)
        pdf.setTitle(title)

        y = 800
        pdf.setFont("Helvetica-Bold", 14)
        pdf.drawString(50, y, "Résumé de la conversation")
        y -= 30

        pdf.setFont("Helvetica", 10)

        for line in text_content.splitlines():
            # Éviter de dépasser les limites de la page
            if y < 50:
                pdf.showPage()
                pdf.setFont("Helvetica", 10)
                y = 800

            pdf.drawString(50, y, line[:100])
            y -= 15

        pdf.save()
        buffer.seek(0)

        return send_file(
            buffer,
            mimetype="application/pdf",
            as_attachment=True,
            download_name="conversation.pdf"
        )

    return jsonify({"error": "Format non pris en charge."}), 400


# -------------------------
# 9. Indicateurs de performance
# -------------------------

@app.route("/stats/<int:conversation_id>")
def get_stats(conversation_id):
    conn = get_db()

    stats = conn.execute(
        """SELECT
               COUNT(*) AS total_messages,
               SUM(tokens) AS total_tokens,
               AVG(duration) AS average_duration,
               AVG(rating) AS average_rating
           FROM messages
           WHERE conversation_id = ? AND role = 'assistant'""",
        (conversation_id,)
    ).fetchone()

    conn.close()

    return jsonify({
        "total_messages": stats["total_messages"] or 0,
        "total_tokens": stats["total_tokens"] or 0,
        "average_duration": round(stats["average_duration"] or 0, 2),
        "average_rating": round(stats["average_rating"] or 0, 2)
    })


if __name__ == "__main__":
    app.run(debug=True)
