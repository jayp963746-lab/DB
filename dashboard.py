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
    # Failsafe: If Discord sent the code to '/' instead of '/callback', forward it automatically!
    code = request.args.get("code")
    if code:
        return redirect(f"/callback?code={code}")
        
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
@app.route("/api/guild/<guild_id>/full_settings")
def api_full_settings(guild_id):
    if "user" not in session: 
        return jsonify({"error": "unauthorized"}), 401
    
    db = get_db()
    config = get_config_row("guild_config", guild_id)
    antinuke = get_config_row("antinuke_config", guild_id)
    antiraid = get_config_row("antiraid_config", guild_id)
    
    words_rows = db.execute("SELECT word FROM banned_words WHERE guild_id=?", (guild_id,)).fetchall()
    banned_words = [w["word"] for w in words_rows]
    
    wl_rows = db.execute("SELECT user_id FROM antinuke_whitelist WHERE guild_id=?", (guild_id,)).fetchall()
    antinuke_wl = [str(w["user_id"]) for w in wl_rows]
    
    warns = db.execute("SELECT user_id, reason, created_at FROM warnings WHERE guild_id=? ORDER BY created_at DESC LIMIT 10", (guild_id,)).fetchall()
    
    db.close()
    
    return jsonify({
        "config": {
            "welcome_channel_id": config.get("welcome_channel_id") or "",
            "welcome_message": config.get("welcome_message") or "",
            "welcome_style": config.get("welcome_style") or "plain",
            "welcome_color": config.get("welcome_color") or "#7B5FFF",
            "welcome_banner_url": config.get("welcome_banner_url") or "",
            "welcome_show_count": bool(config.get("welcome_show_count", 1)),
            "welcome_title": config.get("welcome_title") or "",
            "welcome_author_text": config.get("welcome_author_text") or "",
            "welcome_footer_text": config.get("welcome_footer_text") or "",
            "leave_channel_id": config.get("leave_channel_id") or "",
            "leave_message": config.get("leave_message") or "",
            "log_channel_id": config.get("log_channel_id") or "",
            "automod_enabled": bool(config.get("automod_enabled")),
            "block_invites": bool(config.get("block_invites")),
            "autorole_id": config.get("autorole_id") or "",
            "block_staff_mentions": bool(config.get("block_staff_mentions")),
        },
        "banned_words": banned_words,
        "antinuke": {
            "enabled": bool(antinuke.get("enabled")),
            "log_channel_id": antinuke.get("log_channel_id") or "",
            "action": antinuke.get("action") or "kick",
            "ban_threshold": antinuke.get("ban_threshold", 3),
            "channel_delete_threshold": antinuke.get("channel_delete_threshold", 3),
            "role_delete_threshold": antinuke.get("role_delete_threshold", 3),
            "whitelist": antinuke_wl
        },
        "antiraid": {
            "enabled": bool(antiraid.get("enabled")),
            "log_channel_id": antiraid.get("log_channel_id") or "",
            "join_threshold": antiraid.get("join_threshold", 10),
            "join_window": antiraid.get("join_window", 10),
            "action": antiraid.get("action") or "kick",
            "min_account_age_days": antiraid.get("min_account_age_days", 7)
        },
        "warnings": [dict(w) for w in warns]
    })


@app.route("/api/guild/<guild_id>/save_module/<module>", methods=["POST"])
def api_save_module(guild_id, module):
    if "user" not in session: 
        return jsonify({"error": "unauthorized"}), 401
    
    data = request.json or {}
    db = get_db()
    
    try:
        if module == "general":
            db.execute("""
                INSERT INTO guild_config (guild_id, log_channel_id, autorole_id)
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    log_channel_id=excluded.log_channel_id,
                    autorole_id=excluded.autorole_id
            """, (
                guild_id,
                int(data.get("log_channel_id")) if data.get("log_channel_id") else None,
                int(data.get("autorole_id")) if data.get("autorole_id") else None
            ))
            
        elif module == "automod":
            db.execute("""
                INSERT INTO guild_config (guild_id, automod_enabled, block_invites, block_staff_mentions)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    automod_enabled=excluded.automod_enabled,
                    block_invites=excluded.block_invites,
                    block_staff_mentions=excluded.block_staff_mentions
            """, (
                guild_id,
                int(data.get("automod_enabled", False)),
                int(data.get("block_invites", False)),
                int(data.get("block_staff_mentions", False))
            ))
            
        elif module == "banned_words_add":
            words = [w.strip().lower() for w in data.get("words", "").split(",") if w.strip()]
            for word in words:
                db.execute("INSERT OR IGNORE INTO banned_words (guild_id, word) VALUES (?, ?)", (guild_id, word))
                
        elif module == "banned_words_remove":
            word = data.get("word", "").strip().lower()
            db.execute("DELETE FROM banned_words WHERE guild_id=? AND word=?", (guild_id, word))
            
        elif module == "antinuke":
            db.execute("""
                INSERT INTO antinuke_config (guild_id, enabled, log_channel_id, action, ban_threshold, channel_delete_threshold, role_delete_threshold)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    enabled=excluded.enabled,
                    log_channel_id=excluded.log_channel_id,
                    action=excluded.action,
                    ban_threshold=excluded.ban_threshold,
                    channel_delete_threshold=excluded.channel_delete_threshold,
                    role_delete_threshold=excluded.role_delete_threshold
            """, (
                guild_id,
                int(data.get("enabled", False)),
                int(data.get("log_channel_id")) if data.get("log_channel_id") else None,
                data.get("action", "kick"),
                int(data.get("ban_threshold", 3)),
                int(data.get("channel_delete_threshold", 3)),
                int(data.get("role_delete_threshold", 3))
            ))
            
        elif module == "antinuke_wl_add":
            user_id = data.get("user_id")
            if user_id:
                db.execute("INSERT OR IGNORE INTO antinuke_whitelist (guild_id, user_id) VALUES (?, ?)", (guild_id, int(user_id)))
                
        elif module == "antinuke_wl_remove":
            user_id = data.get("user_id")
            if user_id:
                db.execute("DELETE FROM antinuke_whitelist WHERE guild_id=? AND user_id=?", (guild_id, int(user_id)))
                
        elif module == "antiraid":
            db.execute("""
                INSERT INTO antiraid_config (guild_id, enabled, log_channel_id, action, join_threshold, join_window, min_account_age_days)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    enabled=excluded.enabled,
                    log_channel_id=excluded.log_channel_id,
                    action=excluded.action,
                    join_threshold=excluded.join_threshold,
                    join_window=excluded.join_window,
                    min_account_age_days=excluded.min_account_age_days
            """, (
                guild_id,
                int(data.get("enabled", False)),
                int(data.get("log_channel_id")) if data.get("log_channel_id") else None,
                data.get("action", "kick"),
                int(data.get("join_threshold", 10)),
                int(data.get("join_window", 10)),
                int(data.get("min_account_age_days", 7))
            ))
            
        elif module == "welcome_leave":
            db.execute("""
                INSERT INTO guild_config (
                    guild_id, welcome_channel_id, welcome_message, welcome_style, welcome_color,
                    welcome_banner_url, welcome_show_count, welcome_title, welcome_author_text,
                    welcome_footer_text, leave_channel_id, leave_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    welcome_channel_id=excluded.welcome_channel_id,
                    welcome_message=excluded.welcome_message,
                    welcome_style=excluded.welcome_style,
                    welcome_color=excluded.welcome_color,
                    welcome_banner_url=excluded.welcome_banner_url,
                    welcome_show_count=excluded.welcome_show_count,
                    welcome_title=excluded.welcome_title,
                    welcome_author_text=excluded.welcome_author_text,
                    welcome_footer_text=excluded.welcome_footer_text,
                    leave_channel_id=excluded.leave_channel_id,
                    leave_message=excluded.leave_message
            """, (
                guild_id,
                int(data.get("welcome_channel_id")) if data.get("welcome_channel_id") else None,
                data.get("welcome_message", ""),
                data.get("welcome_style", "plain"),
                data.get("welcome_color", "#7B5FFF"),
                data.get("welcome_banner_url") or None,
                int(data.get("welcome_show_count", True)),
                data.get("welcome_title") or None,
                data.get("welcome_author_text") or None,
                data.get("welcome_footer_text") or None,
                int(data.get("leave_channel_id")) if data.get("leave_channel_id") else None,
                data.get("leave_message", "")
            ))
            
        elif module == "tags":
            db.execute("INSERT INTO tags (guild_id, name, content, creator_id) VALUES (?, ?, ?, ?)", 
                       (guild_id, data.get("name").lower(), data.get("content"), session["user"]["id"]))
                       
        elif module == "reactionrole":
            db.execute("INSERT OR REPLACE INTO reaction_roles (message_id, emoji, role_id, guild_id) VALUES (?,?,?,?)",
                       (int(data.get("message_id")), data.get("emoji"), int(data.get("role_id")), guild_id))
            
        db.commit()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    finally:
        db.close()
    
    
if __name__ == "__main__":
    from waitress import serve
    port = int(os.getenv("PORT", 10000))
    print(f"Starting Dashboard on port {port}...")
    serve(app, host="0.0.0.0", port=port)
