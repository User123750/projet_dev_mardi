import os
import io
import re
import json
import time
import sqlite3
from datetime import datetime
from xml.sax.saxutils import escape

import pandas as pd
from flask import Flask, render_template, request, jsonify, send_file
from openai import (OpenAI, AuthenticationError, RateLimitError,
                    APIConnectionError, APITimeoutError, APIStatusError)
from dotenv import load_dotenv
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

load_dotenv()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 15 * 1024 * 1024
DATABASE = "flaskgpt.db"
MODEL = "openai/gpt-oss-20b"

# Clé factice si absente : l'application démarre et affiche une erreur claire
client = OpenAI(api_key=os.getenv("GROQ_API_KEY") or "missing",
                base_url="https://api.groq.com/openai/v1", timeout=60)

DOMAINS = {
    "general": "Tu es un assistant utile et pédagogique.",
    "informatique": "Tu es un assistant expert en informatique : code, architecture, réseaux, bonnes pratiques.",
    "finance": "Tu es un assistant expert en finance : comptabilité, marchés, analyse financière.",
    "data": "Tu es un assistant expert en analyse de données : statistiques, qualité, SQL, visualisation.",
    "ia": "Tu es un assistant expert en intelligence artificielle et machine learning.",
    "droit": "Tu es un assistant expert en droit. Rappelle que tu ne remplaces pas un avocat.",
}


# ---------- Base de données ----------

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT, domain TEXT, created_at TEXT);
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER, role TEXT, content TEXT,
            duration REAL, tokens INTEGER DEFAULT 0,
            rating INTEGER DEFAULT 0, created_at TEXT);
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT, created_at TEXT);
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER, idx INTEGER, text TEXT);
    """)
    # Migration : ajout des nouvelles colonnes aux anciennes bases
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(messages)")}
    for name, sql in [("prompt_tokens", "INTEGER DEFAULT 0"),
                      ("completion_tokens", "INTEGER DEFAULT 0"),
                      ("sources", "TEXT")]:
        if name not in cols:
            conn.execute(f"ALTER TABLE messages ADD COLUMN {name} {sql}")
    conn.commit()
    conn.close()


init_db()


# ---------- Utilitaires ----------

class NoKey(Exception):
    pass


def api_error(e):
    """Transforme une erreur de l'API en message clair + code HTTP."""
    print("Erreur API :", repr(e))
    if isinstance(e, NoKey):
        return "La clé API Groq est absente du fichier .env.", 500
    if isinstance(e, AuthenticationError):
        return "Clé API invalide. Vérifie GROQ_API_KEY dans le fichier .env.", 401
    if isinstance(e, RateLimitError):
        return "Quota ou limite de requêtes dépassé. Attends un peu ou vérifie ton compte Groq.", 429
    if isinstance(e, (APIConnectionError, APITimeoutError)):
        return "Problème de connexion à l'API. Vérifie ta connexion Internet.", 503
    if isinstance(e, APIStatusError):
        return f"Le service IA a renvoyé une erreur ({e.status_code}). Réessaie.", 502
    return "Impossible de générer la réponse. Réessaie.", 500


def ask_llm(messages):
    if not os.getenv("GROQ_API_KEY"):
        raise NoKey()
    start = time.time()
    r = client.chat.completions.create(model=MODEL, messages=messages)
    u = r.usage
    return (r.choices[0].message.content or "", round(time.time() - start, 2),
            (u.prompt_tokens or 0) if u else 0,
            (u.completion_tokens or 0) if u else 0)


def clean(text):
    table = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"',
                           "–": "-", "—": "-", "…": "...", "œ": "oe", "•": "-"})
    return (text or "").translate(table).encode("latin-1", "replace").decode("latin-1")


def build_pdf(title, blocks):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, title=clean(title))
    st = getSampleStyleSheet()
    story = [Paragraph(escape(clean(title)), st["Title"])]
    for head, body in blocks:
        if head:
            story.append(Paragraph(f"<b>{escape(clean(head))}</b>", st["Heading4"]))
        story.append(Paragraph(escape(clean(body)).replace("\n", "<br/>"), st["BodyText"]))
        story.append(Spacer(1, 8))
    doc.build(story)
    buf.seek(0)
    return buf


def load_conversation(conn, cid):
    conv = conn.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()
    if conv is None:
        return None, None
    msgs = []
    for m in conn.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY id", (cid,)):
        d = dict(m)
        d["sources"] = json.loads(d["sources"]) if d.get("sources") else []
        msgs.append(d)
    return dict(conv), msgs


# ---------- Pages et conversations ----------

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/conversations", methods=["GET"])
def list_conversations():
    conn = get_db()
    rows = conn.execute("""SELECT * FROM conversations c WHERE EXISTS
        (SELECT 1 FROM messages m WHERE m.conversation_id=c.id)
        ORDER BY id DESC LIMIT 50""").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/conversations", methods=["POST"])
def new_conversation():
    data = request.get_json(silent=True) or {}
    title = data.get("title", "Nouvelle conversation")
    domain = data.get("domain", "general")
    conn = get_db()
    cur = conn.execute("INSERT INTO conversations (title, domain, created_at) VALUES (?,?,?)",
                       (title, domain, datetime.now().isoformat()))
    conn.commit()
    cid = cur.lastrowid
    conn.close()
    return jsonify({"id": cid, "title": title, "domain": domain})


@app.route("/conversations/<int:cid>", methods=["GET"])
def get_conversation(cid):
    conn = get_db()
    conv, msgs = load_conversation(conn, cid)
    conn.close()
    if conv is None:
        return jsonify({"error": "Conversation introuvable."}), 404
    return jsonify({"conversation": conv, "messages": msgs})


@app.route("/conversations/<int:cid>", methods=["DELETE"])
def delete_conversation(cid):
    conn = get_db()
    conn.execute("DELETE FROM messages WHERE conversation_id=?", (cid,))
    conn.execute("DELETE FROM conversations WHERE id=?", (cid,))
    conn.commit()
    conn.close()
    return jsonify({"message": "Conversation supprimée."})


# ---------- Sources (documents) ----------

def split_chunks(text, size=700):
    out = []
    for p in re.split(r"\n\s*\n", text):
        p = p.strip()
        while len(p) > size:
            cut = p.rfind(" ", 0, size)
            cut = cut if cut > 0 else size
            out.append(p[:cut])
            p = p[cut:].strip()
        if p:
            out.append(p)
    return out


def words(t):
    return set(re.findall(r"\w{4,}", t.lower()))


def retrieve(question, doc_ids, k):
    q, res = words(question), []
    conn = get_db()
    for d in doc_ids:
        doc = conn.execute("SELECT name FROM documents WHERE id=?", (d,)).fetchone()
        if not doc:
            continue
        rows = conn.execute("SELECT idx, text FROM chunks WHERE document_id=?", (d,)).fetchall()
        scored = sorted(((len(q & words(r["text"])), r) for r in rows), key=lambda x: -x[0])
        for score, r in scored[:k]:
            if score > 0:
                res.append({"document": doc["name"], "passage": r["idx"] + 1,
                            "text": r["text"], "score": score})
    conn.close()
    for i, r in enumerate(res, 1):
        r["tag"] = f"S{i}"
    return res


@app.route("/documents", methods=["GET"])
def list_documents():
    conn = get_db()
    rows = conn.execute("SELECT * FROM documents ORDER BY id DESC").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/documents", methods=["POST"])
def upload_document():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "Choisis un fichier PDF, TXT ou MD."}), 400
    ext = f.filename.rsplit(".", 1)[-1].lower()
    try:
        if ext == "pdf":
            text = "\n\n".join((p.extract_text() or "") for p in PdfReader(f).pages)
        elif ext in ("txt", "md"):
            text = f.read().decode("utf-8", errors="ignore")
        else:
            return jsonify({"error": "Format non pris en charge (PDF, TXT, MD)."}), 400
    except Exception:
        return jsonify({"error": "Impossible de lire ce fichier."}), 400
    chunks = split_chunks(text)
    if not chunks:
        return jsonify({"error": "Aucun texte exploitable dans ce document."}), 400
    conn = get_db()
    cur = conn.execute("INSERT INTO documents (name, created_at) VALUES (?,?)",
                       (f.filename, datetime.now().isoformat()))
    conn.executemany("INSERT INTO chunks (document_id, idx, text) VALUES (?,?,?)",
                     [(cur.lastrowid, i, c) for i, c in enumerate(chunks)])
    conn.commit()
    conn.close()
    return jsonify({"id": cur.lastrowid, "name": f.filename, "chunks": len(chunks)})


@app.route("/documents/<int:did>", methods=["DELETE"])
def delete_document(did):
    conn = get_db()
    conn.execute("DELETE FROM chunks WHERE document_id=?", (did,))
    conn.execute("DELETE FROM documents WHERE id=?", (did,))
    conn.commit()
    conn.close()
    return jsonify({"message": "Document supprimé."})


# ---------- Chat ----------

@app.route("/prompt", methods=["POST"])
def prompt():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Données invalides."}), 400
    cid = data.get("conversation_id")
    message = (data.get("message") or "").strip()
    domain = data.get("domain", "general")
    doc_ids = [int(d) for d in data.get("source_ids", []) if str(d).isdigit()]
    k = max(1, min(int(data.get("top_k", 3) or 3), 10))

    if not message:
        return jsonify({"error": "Écris une question."}), 400
    conn = get_db()
    conv, msgs = load_conversation(conn, cid) if cid else (None, None)
    if conv is None:
        conn.close()
        return jsonify({"error": "Conversation introuvable."}), 404
    conn.close()

    sources = retrieve(message, doc_ids, k) if doc_ids else []
    system = DOMAINS.get(domain, DOMAINS["general"])
    if sources:
        system += ("\n\nAppuie-toi sur les sources ci-dessous et cite-les avec [S1], [S2]... "
                   "Si elles ne suffisent pas, dis-le clairement.\n\n" +
                   "\n\n".join(f"[{s['tag']}] ({s['document']}, passage {s['passage']})\n{s['text']}"
                               for s in sources))
    history = [{"role": m["role"], "content": m["content"]} for m in msgs]
    history.append({"role": "user", "content": message})

    try:
        answer, duration, pt, ct = ask_llm([{"role": "system", "content": system}] + history)
    except Exception as e:
        text, code = api_error(e)
        return jsonify({"error": text}), code

    now = datetime.now().isoformat()
    conn = get_db()
    conn.execute("INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?,?,?,?)",
                 (cid, "user", message, now))
    cur = conn.execute(
        """INSERT INTO messages (conversation_id, role, content, duration, tokens,
           prompt_tokens, completion_tokens, sources, created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
        (cid, "assistant", answer, duration, pt + ct, pt, ct,
         json.dumps(sources, ensure_ascii=False), now))
    conn.execute("UPDATE conversations SET domain=? WHERE id=?", (domain, cid))
    if conv["title"] == "Nouvelle conversation":
        conn.execute("UPDATE conversations SET title=? WHERE id=?", (message[:40], cid))
    conn.commit()
    mid = cur.lastrowid
    conn.close()
    return jsonify({"id": mid, "answer": answer, "duration": duration, "tokens": pt + ct,
                    "prompt_tokens": pt, "completion_tokens": ct, "sources": sources})


@app.route("/messages/<int:mid>/rating", methods=["POST"])
def rate_message(mid):
    rating = (request.get_json(silent=True) or {}).get("rating")
    if not isinstance(rating, int) or not 1 <= rating <= 5:
        return jsonify({"error": "La note doit être entre 1 et 5."}), 400
    conn = get_db()
    cur = conn.execute("UPDATE messages SET rating=? WHERE id=? AND role='assistant'", (rating, mid))
    conn.commit()
    conn.close()
    if not cur.rowcount:
        return jsonify({"error": "Réponse introuvable."}), 404
    return jsonify({"message": "Note enregistrée."})


# ---------- Export et résumé PDF ----------

@app.route("/conversations/<int:cid>/export/<fmt>")
def export_conversation(cid, fmt):
    conn = get_db()
    conv, msgs = load_conversation(conn, cid)
    conn.close()
    if conv is None:
        return jsonify({"error": "Conversation introuvable."}), 404
    title = conv["title"] or "Conversation"

    if fmt == "json":
        return app.response_class(
            json.dumps({"conversation": conv, "messages": msgs}, ensure_ascii=False, indent=2),
            mimetype="application/json",
            headers={"Content-Disposition": "attachment; filename=conversation.json"})
    if fmt == "txt":
        text = "\n\n".join(f"{'VOUS' if m['role'] == 'user' else 'ASSISTANT'} : {m['content']}"
                           for m in msgs)
        return app.response_class(
            text, mimetype="text/plain; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=conversation.txt"})
    if fmt == "pdf":
        blocks = []
        for m in msgs:
            head = "Vous" if m["role"] == "user" else \
                f"Assistant ({m['duration'] or 0} s, {m['tokens'] or 0} tokens, note {m['rating'] or '-'}/5)"
            blocks.append((head, m["content"]))
        return send_file(build_pdf(title, blocks), mimetype="application/pdf",
                         as_attachment=True, download_name="conversation.pdf")
    return jsonify({"error": "Format non pris en charge."}), 400


@app.route("/conversations/<int:cid>/summary")
def summary_pdf(cid):
    conn = get_db()
    conv, msgs = load_conversation(conn, cid)
    conn.close()
    if conv is None:
        return jsonify({"error": "Conversation introuvable."}), 404
    transcript = "\n".join(f"{m['role']}: {m['content']}" for m in msgs)[-12000:]
    try:
        text, _, _, _ = ask_llm([
            {"role": "system", "content": "Résume cette conversation en français : "
             "contexte, points clés, conclusions. Sois concis et structuré."},
            {"role": "user", "content": transcript}])
    except Exception as e:
        msg, code = api_error(e)
        return jsonify({"error": msg}), code
    return send_file(build_pdf(f"Résumé : {conv['title']}", [("Domaine : " + conv["domain"], text)]),
                     mimetype="application/pdf", as_attachment=True, download_name="resume.pdf")


# ---------- Performances ----------

@app.route("/stats/<int:cid>")
def get_stats(cid):
    conn = get_db()
    rows = conn.execute("""SELECT id, duration, tokens, prompt_tokens, completion_tokens, rating
        FROM messages WHERE conversation_id=? AND role='assistant' ORDER BY id""", (cid,)).fetchall()
    conn.close()
    items = [dict(r) for r in rows]
    n = len(items)
    rated = [i["rating"] for i in items if i["rating"]]
    return jsonify({
        "total_messages": n,
        "total_tokens": sum(i["tokens"] or 0 for i in items),
        "average_duration": round(sum(i["duration"] or 0 for i in items) / n, 2) if n else 0,
        "average_rating": round(sum(rated) / len(rated), 2) if rated else 0,
        "items": items})


# ---------- Analyse et gouvernance des données ----------

@app.route("/analyze", methods=["POST"])
def analyze():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "Choisis un fichier CSV ou Excel."}), 400
    name = f.filename.lower()
    try:
        if name.endswith(".csv"):
            df = pd.read_csv(f, sep=None, engine="python")
        elif name.endswith((".xlsx", ".xls")):
            df = pd.read_excel(f)
        else:
            return jsonify({"error": "Format non pris en charge (CSV, XLSX)."}), 400
    except Exception:
        return jsonify({"error": "Impossible de lire ce fichier. Vérifie son format."}), 400
    if df.empty:
        return jsonify({"error": "Le fichier ne contient aucune donnée."}), 400

    df = df.replace(r"^\s*$", pd.NA, regex=True)
    rows, cols = df.shape
    cells = rows * cols
    missing = {c: int(df[c].isna().sum()) for c in df.columns}
    miss_total = sum(missing.values())
    dup = int(df.duplicated().sum())
    issues, anomalies, bad_cells = [], [], 0

    for c in df.columns:
        s = df[c].dropna()
        if s.empty:
            continue
        num = s if pd.api.types.is_numeric_dtype(s) else None
        if num is None:
            conv = pd.to_numeric(s, errors="coerce")
            ratio = conv.notna().mean()
            if ratio >= 0.8:
                bad = int(conv.isna().sum())
                if bad:
                    issues.append({"column": str(c), "count": bad,
                                   "issue": "valeurs non numériques dans une colonne numérique"})
                    bad_cells += bad
                num = conv.dropna()
            else:
                st = s.astype(str)
                spaces = int((st != st.str.strip()).sum())
                if spaces:
                    issues.append({"column": str(c), "count": spaces,
                                   "issue": "espaces en début ou fin de valeur"})
                    bad_cells += spaces
                stripped = st.str.strip()
                if stripped.str.lower().nunique() < stripped.nunique():
                    issues.append({"column": str(c), "count": 1,
                                   "issue": "même valeur écrite avec des casses différentes"})
                    bad_cells += 1
        if s.nunique() == 1 and rows > 1:
            issues.append({"column": str(c), "count": 0, "issue": "colonne constante (une seule valeur)"})
        if num is not None and len(num) > 3:
            q1, q3 = num.quantile(0.25), num.quantile(0.75)
            iqr = q3 - q1
            if iqr > 0:
                out = num[(num < q1 - 1.5 * iqr) | (num > q3 + 1.5 * iqr)]
                if len(out):
                    anomalies.append({"column": str(c), "count": int(len(out)),
                                      "examples": [float(x) for x in out.head(3)]})

    completeness = round(100 * (1 - miss_total / cells), 1)
    uniqueness = round(100 * (1 - dup / rows), 1)
    validity = round(100 * (1 - min(bad_cells, cells) / cells), 1)
    score = round((completeness + uniqueness + validity) / 3, 1)
    level = "Bonne" if score >= 90 else "Moyenne" if score >= 70 else "Faible"
    return jsonify({
        "rows": rows, "columns": cols, "missing": missing, "missing_total": miss_total,
        "duplicates": dup, "issues": issues, "anomalies": anomalies,
        "completeness": completeness, "uniqueness": uniqueness, "validity": validity,
        "score": score, "level": level})


@app.errorhandler(413)
def too_large(_):
    return jsonify({"error": "Fichier trop volumineux (15 Mo maximum)."}), 413


if __name__ == "__main__":
    app.run(debug=True)
