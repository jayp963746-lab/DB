import os
import secrets
import sqlite3
import requests
from flask import Flask, render_template, jsonify, session, request, redirect

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.getenv("FLASK_SECRET_KEY", secrets.token_hex(16))

_bot = None

def set_bot(bot_instance):
    global _bot
    _bot = bot_instance

def get_db():
    conn = sqlite3.connect(os.getenv("BOT_DB_PATH", "bot.db"))
    conn.row_factory = sqlite3.Row
    return conn

def get_config_row(table, guild_id):
    try:
        db = get_db()
        row = db.execute(f"SELECT * FROM {table} WHERE guild_id=?", (guild_id,)).fetchone()
        db.close()
        return dict(row) if row else {}
    except:
        return {}

# --- HTML ROUTE ---
@app.route("/")
def index():
    return render_template("index.html", logged_in="user" in session)

# --- OAUTH LOGIN ROUTES ---
@app.route("/login")
def login():
    client_id = os.getenv("DISCORD_CLIENT_ID")
    redirect_uri = os.getenv("DISCORD_REDIRECT_URI")
    if not client_id or not redirect_uri:
        return "Discord OAuth variables are missing in your Render environment!", 500
        
    state = secrets.token_urlsafe(16)
    session["oauth_state"] = state
    url = f"https://discord.com/api/oauth2/authorize?client_id={client_id}&redirect_uri={redirect_uri}&response_type=code&scope=identify%20guilds&state={state}&prompt=consent"
    return redirect(url)

@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code: 
        return "No code provided by Discord", 400
        
    data = {
        "client_id": os.getenv("DISCORD_CLIENT_ID"),
        "client_secret": os.getenv("DISCORD_CLIENT_SECRET"),
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": os.getenv("DISCORD_REDIRECT_URI")
    }
    res = requests.post("https://discord.com/api/oauth2/token", data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    
    # If Discord token exchange fails, display the exact error message!
    if not res.ok:
        return f"<h3>Discord OAuth Failed:</h3><p>{res.status_code} - {res.text}</p><p>Check your DISCORD_REDIRECT_URI and DISCORD_CLIENT_SECRET in Render.</p>", 400
        
    access_token = res.json().get("access_token")
    user_res = requests.get("https://discord.com/api/users/@me", headers={"Authorization": f"Bearer {access_token}"})
    guilds_res = requests.get("https://discord.com/api/users/@me/guilds", headers={"Authorization": f"Bearer {access_token}"})
    
    if user_res.ok and guilds_res.ok:
        session["user"] = user_res.json()
        admin_guilds = [g for g in guilds_res.json() if (int(g.get("permissions", 0)) & 0x8) == 0x8]
        session["guilds"] = admin_guilds
        return redirect("/")
    else:
        return "Failed to fetch user profile or server list from Discord API.", 400
        

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

# --- API ROUTES ---
@app.route("/api/user")
def api_user():
    if "user" not in session:
        return jsonify({"error": "unauthorized"}), 401
        
    user_guilds = session.get("guilds", [])
    
    if _bot:
        bot_guild_ids = [str(g.id) for g in _bot.guilds]
        filtered_guilds = [g for g in user_guilds if str(g["id"]) in bot_guild_ids]
    else:
        filtered_guilds = user_guilds
        
    return jsonify({
        "user": session["user"],
        "guilds": filtered_guilds
    })

@app.route("/api/guild/<guild_id>/overview")
def api_overview(guild_id):
    if "user" not in session: return jsonify({"error": "unauthorized"}), 401

    try:
        db = get_db()
        warns = db.execute("SELECT COUNT(*) FROM warnings WHERE guild_id=?", (guild_id,)).fetchone()[0]
        gives = db.execute("SELECT COUNT(*) FROM giveaways WHERE guild_id=? AND ended=0", (guild_id,)).fetchone()[0]
        tags = db.execute("SELECT COUNT(*) FROM tags WHERE guild_id=?", (guild_id,)).fetchone()[0]
        db.close()
    except:
        warns, gives, tags = 0, 0, 0

    config = get_config_row("guild_config", guild_id)
    antinuke = get_config_row("antinuke_config", guild_id)
    antiraid = get_config_row("antiraid_config", guild_id)
    
    members_count = "Live"
    if _bot:
        guild_obj = _bot.get_guild(int(guild_id))
        if guild_obj: members_count = guild_obj.member_count

    return jsonify({
        "members": members_count,
        "warnings_issued": warns,
        "active_giveaways": gives,
        "tags": tags,
        "automod_on": bool(config.get("automod_enabled")),
        "antinuke_on": bool(antinuke.get("enabled")),
        "antiraid_on": bool(antiraid.get("enabled"))
    })

if __name__ == "__main__":
    from waitress import serve
    port = int(os.getenv("PORT", 10000))
    print(f"Starting Dashboard on port {port}...")
    serve(app, host="0.0.0.0", port=port)
