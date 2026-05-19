import os, json, random, time, urllib.request
from datetime import datetime
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

def redis_get(key):
    url = os.environ.get("UPSTASH_REDIS_REST_URL", "")
    token = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")
    req = urllib.request.Request(
        f"{url}/get/{key}",
        headers={"Authorization": f"Bearer {token}"}
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read()).get("result")

def redis_set(key, value):
    url = os.environ.get("UPSTASH_REDIS_REST_URL", "")
    token = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")
    body = json.dumps([["SET", key, value]]).encode()
    req = urllib.request.Request(
        f"{url}/pipeline",
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())

@app.route("/debug")
def debug():
    has_url = bool(os.environ.get("UPSTASH_REDIS_REST_URL"))
    has_token = bool(os.environ.get("UPSTASH_REDIS_REST_TOKEN"))
    return jsonify({"has_url": has_url, "has_token": has_token})

COLORS = ['#cc1100','#1177cc','#11aa44','#cc7700','#9911cc',
          '#11ccaa','#cc1177','#4477cc','#88cc11','#cc4411']

def load_sessions():
    try:
        data = redis_get("sessions")
        result = json.loads(data) if data else {}
        return result if isinstance(result, dict) else {}
    except:
        return {}

def save_sessions(sessions):
    redis_set("sessions", json.dumps(sessions))

def load_messages():
    try:
        data = redis_get("messages")
        result = json.loads(data) if data else []
        return result if isinstance(result, list) else []
    except:
        return []

def save_messages(msgs):
    redis_set("messages", json.dumps(msgs))

def cleanup_sessions(sessions):
    # Give 10 seconds before expiring
    now = time.time()
    return {k: v for k, v in sessions.items() if now - v.get('last_seen', 0) < 30}

def get_color(name):
    return COLORS[abs(hash(name)) % len(COLORS)]

def upsert_session(sessions, name, desk=None):
    """Add or refresh a player session, always returns updated sessions"""
    existing = sessions.get(name, {})
    sessions[name] = {
        "name": name,
        "color": get_color(name),
        "last_seen": time.time(),
        "ants": existing.get("ants", {}),
        "desk": desk or existing.get("desk", ""),
    }
    return sessions

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/join", methods=["POST"])
def join():
    data = request.json
    name = data.get("name", "Unknown").strip()
    desk = data.get("desk", "").strip()
    if not name:
        return jsonify({"error": "no name"}), 400
    sessions = load_sessions()
    sessions = cleanup_sessions(sessions)
    sessions = upsert_session(sessions, name, desk)
    save_sessions(sessions)
    print(f"[JOIN] {name} desk={desk} — sessions now: {list(sessions.keys())}")
    return jsonify({"color": get_color(name), "players": list(sessions.values())})

@app.route("/api/state")
def get_state():
    me = request.args.get("me", "")
    sessions = load_sessions()
    sessions = cleanup_sessions(sessions)
    # Refresh our own last_seen if we're in state
    if me and me in sessions:
        sessions[me]["last_seen"] = time.time()
        save_sessions(sessions)
    others = [v for k, v in sessions.items() if k != me]
    messages = load_messages()
    recent = [m for m in messages if m["to"] == me and m["status"] == "delivered"]
    recent.sort(key=lambda x: x["timestamp"], reverse=True)
    print(f"[STATE] requested by '{me}' — all players: {list(sessions.keys())}")
    return jsonify({
        "players": list(sessions.values()),
        "others": others,
        "recent_for_me": recent[:5],
    })

@app.route("/api/ant_update", methods=["POST"])
def ant_update():
    data = request.json
    name = data.get("player", "").strip()
    if not name:
        return jsonify({"ok": False})
    sessions = load_sessions()
    sessions = cleanup_sessions(sessions)
    # Always upsert — never silently drop
    sessions = upsert_session(sessions, name)
    sessions[name]["ants"] = data.get("ants", {})
    save_sessions(sessions)
    return jsonify({"ok": True})

@app.route("/api/leave", methods=["POST"])
def leave():
    data = request.json
    name = data.get("name", "")
    sessions = load_sessions()
    sessions.pop(name, None)
    save_sessions(sessions)
    print(f"[LEAVE] {name}")
    return jsonify({"ok": True})

@app.route("/api/messages")
def get_messages():
    name = request.args.get("name", "")
    messages = load_messages()
    inbox = [m for m in messages if m["to"] == name and m["status"] == "delivered"]
    sent  = [m for m in messages if m["from"] == name]
    return jsonify({"inbox": inbox, "sent": sent})

@app.route("/api/all")
def get_all():
    return jsonify(load_messages())

@app.route("/api/send", methods=["POST"])
def send_message():
    data = request.json
    messages = load_messages()
    forced = data.get("forced_outcome")

    if forced == "squished":
        status = "squished"
        squish_msg = random.choice([
            "🥾 Squished by a rolling chair!",
            "👟 A shoe got them near the break room.",
            "📦 Crushed by a falling box.",
            "🧹 Swept away by the broom.",
        ])
    elif forced == "garbled":
        status = "garbled"
        squish_msg = random.choice([
            "🤕 Arrived dazed — message scrambled!",
            "🍕 Got distracted by pizza, lost half the message.",
            "🍩 Stopped for a donut, forgot the rest.",
        ])
        words = data["body"].split()
        random.shuffle(words)
        data["body"] = " ".join(words) + " [MESSAGE PARTIALLY CORRUPTED]"
    else:
        status = "delivered"
        squish_msg = None

    msg = {
        "id": str(int(time.time() * 1000)),
        "from": data["from"],
        "to": data["to"],
        "subject": data.get("subject", ""),
        "body": data["body"],
        "timestamp": datetime.now().isoformat(),
        "status": status,
        "squish_msg": squish_msg,
    }
    messages.append(msg)
    save_messages(messages)
    return jsonify({"status": status, "squish_msg": squish_msg, "message": msg})

if __name__ == "__main__":
    app.run(debug=True, port=5050)