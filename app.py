import json
import os
import re
import sqlite3
import secrets
from datetime import date, datetime, timedelta
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, flash, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()
app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.getenv("SECRET_KEY") or secrets.token_urlsafe(32),
    DATABASE=os.getenv("DATABASE", "life_assistant.db"),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, username TEXT NOT NULL COLLATE NOCASE UNIQUE, password_hash TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, title TEXT NOT NULL, description TEXT DEFAULT '', priority TEXT NOT NULL DEFAULT 'medium', due_date TEXT, completed INTEGER NOT NULL DEFAULT 0, completed_at TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(user_id) REFERENCES users(id));
CREATE TABLE IF NOT EXISTS habits (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, name TEXT NOT NULL, description TEXT DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(user_id) REFERENCES users(id));
CREATE TABLE IF NOT EXISTS habit_checks (id INTEGER PRIMARY KEY AUTOINCREMENT, habit_id INTEGER NOT NULL, check_date TEXT NOT NULL, UNIQUE(habit_id, check_date), FOREIGN KEY(habit_id) REFERENCES habits(id));
CREATE TABLE IF NOT EXISTS goals (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, title TEXT NOT NULL, description TEXT DEFAULT '', progress INTEGER NOT NULL DEFAULT 0 CHECK(progress BETWEEN 0 AND 100), target_date TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(user_id) REFERENCES users(id));
CREATE TABLE IF NOT EXISTS chat_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(user_id) REFERENCES users(id));
"""

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        g.db.executescript(SCHEMA)
        migrate_users(g.db)
        g.db.commit()
    return g.db

@app.teardown_appcontext
def close_db(_error=None):
    db = g.pop("db", None)
    if db: db.close()

def init_db():
    get_db()

@app.cli.command("init-db")
def init_db_command():
    init_db(); print("Database initialized.")

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session or current_user() is None:
            session.clear()
            flash("Please log in first.", "error"); return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped

def current_user():
    return get_db().execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()

def owned(table, item_id):
    allowed = {"tasks", "habits", "goals"}
    if table not in allowed: return None
    return get_db().execute(f"SELECT * FROM {table} WHERE id=? AND user_id=?", (item_id, session["user_id"])).fetchone()

def parse_date(value):
    if not value: return None
    try: return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError: return None

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")

def valid_username(value):
    return bool(USERNAME_RE.fullmatch(value))

def migration_username(value, user_id):
    """Produce a readable, valid username for a legacy email-based account."""
    local_part = (value or "").split("@", 1)[0].lower()
    cleaned = re.sub(r"[^a-z0-9_.-]+", "-", local_part).strip(".-_")
    candidate = cleaned[:32] if len(cleaned) >= 3 else f"user{user_id}"
    return candidate

def migrate_users(db):
    """Add usernames to pre-existing databases without removing user-owned data."""
    columns = {row["name"] for row in db.execute("PRAGMA table_info(users)")}
    if "username" not in columns:
        db.execute("ALTER TABLE users ADD COLUMN username TEXT")
        columns.add("username")

    rows = db.execute("SELECT id, username, email FROM users" if "email" in columns else "SELECT id, username FROM users").fetchall()
    for row in rows:
        if row["username"]:
            continue
        candidate = migration_username(row["email"] if "email" in columns else "", row["id"])
        suffix = 1
        unique_candidate = candidate
        while db.execute("SELECT 1 FROM users WHERE username = ? COLLATE NOCASE", (unique_candidate,)).fetchone():
            suffix_text = f"-{suffix}"
            unique_candidate = f"{candidate[:32-len(suffix_text)]}{suffix_text}"
            suffix += 1
        db.execute("UPDATE users SET username=? WHERE id=?", (unique_candidate, row["id"]))
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_username_unique ON users(username COLLATE NOCASE)")

def legacy_email_for(username):
    """Only used when registering against an old table whose email is NOT NULL."""
    return f"{username}.{secrets.token_hex(8)}@legacy.local"

def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]

@app.context_processor
def inject_template_helpers():
    return {"csrf_token": csrf_token}

@app.before_request
def protect_unsafe_requests():
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        submitted = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
        if not submitted or not secrets.compare_digest(submitted, csrf_token()):
            if request.path.startswith("/api/"):
                return jsonify(error="Your session form token is missing or expired. Refresh the page and try again."), 400
            flash("Your form expired. Please try again.", "error")
            return redirect(request.referrer or url_for("index"))

def data_context():
    db, uid, today = get_db(), session["user_id"], date.today().isoformat()
    tasks = db.execute("SELECT * FROM tasks WHERE user_id=? ORDER BY completed, CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, due_date", (uid,)).fetchall()
    habits = db.execute("SELECT h.*, EXISTS(SELECT 1 FROM habit_checks c WHERE c.habit_id=h.id AND c.check_date=?) checked_today FROM habits h WHERE h.user_id=? ORDER BY h.created_at DESC", (today, uid)).fetchall()
    goals = db.execute("SELECT * FROM goals WHERE user_id=? ORDER BY progress DESC", (uid,)).fetchall()
    return tasks, habits, goals

@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    has_users = get_db().execute("SELECT 1 FROM users LIMIT 1").fetchone()
    return redirect(url_for("login" if has_users else "register"))

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not valid_username(username) or len(password) < 8:
            flash("Choose a username with 3–32 letters, numbers, dots, hyphens, or underscores, and a password with at least 8 characters.", "error")
        else:
            try:
                db = get_db()
                columns = {row["name"] for row in db.execute("PRAGMA table_info(users)")}
                if "email" in columns:
                    db.execute("INSERT INTO users(name,username,email,password_hash) VALUES(?,?,?,?)", (username, username, legacy_email_for(username), generate_password_hash(password)))
                else:
                    db.execute("INSERT INTO users(name,username,password_hash) VALUES(?,?,?)", (username, username, generate_password_hash(password)))
                db.commit()
                flash("Account created. Please log in.", "success"); return redirect(url_for("login"))
            except sqlite3.IntegrityError: flash("That username is already taken. Please choose another one.", "error")
    return render_template("auth.html", mode="register")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        user=get_db().execute("SELECT * FROM users WHERE username=? COLLATE NOCASE", (username,)).fetchone()
        if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
            session.clear(); session["user_id"] = user["id"]; return redirect(url_for("dashboard"))
        flash("Incorrect username or password.", "error")
    return render_template("auth.html", mode="login")

@app.post("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/dashboard")
@login_required
def dashboard():
    tasks, habits, goals = data_context(); today=date.today().isoformat()
    done_today=sum(1 for t in tasks if t["completed_at"] and t["completed_at"][:10] == today)
    total=len(tasks); complete=sum(t["completed"] for t in tasks)
    seven=[]
    for offset in range(6,-1,-1):
        day=(date.today()-timedelta(days=offset)).isoformat()
        seven.append({"day": datetime.strptime(day,"%Y-%m-%d").strftime("%a"), "count": sum(1 for t in tasks if t["completed_at"] and t["completed_at"][:10]==day)})
    return render_template("dashboard.html", user=current_user(), tasks=tasks, habits=habits, goals=goals, done_today=done_today, total=total, complete=complete, chart=seven)

@app.route("/tasks", methods=["GET", "POST"])
@login_required
def tasks():
    if request.method == "POST":
        title=request.form.get("title", "").strip(); priority=request.form.get("priority", "medium")
        if not title: flash("Task title is required.", "error")
        elif priority not in ("low","medium","high"): flash("Invalid priority.", "error")
        else:
            get_db().execute("INSERT INTO tasks(user_id,title,description,priority,due_date) VALUES(?,?,?,?,?)", (session["user_id"],title,request.form.get("description","").strip(),priority,parse_date(request.form.get("due_date")))); get_db().commit(); flash("Task added.", "success")
        return redirect(url_for("tasks"))
    return render_template("tasks.html", tasks=data_context()[0], user=current_user())

@app.post("/tasks/<int:item_id>/toggle")
@login_required
def toggle_task(item_id):
    task=owned("tasks",item_id)
    if not task: return ("Not found",404)
    completed=0 if task["completed"] else 1; stamp=datetime.now().isoformat(timespec="seconds") if completed else None
    get_db().execute("UPDATE tasks SET completed=?,completed_at=? WHERE id=?",(completed,stamp,item_id)); get_db().commit(); return redirect(request.referrer or url_for("tasks"))

@app.route("/tasks/<int:item_id>/edit", methods=["GET", "POST"])
@login_required
def edit_task(item_id):
    task=owned("tasks",item_id)
    if not task: return ("Not found",404)
    if request.method=="POST":
        title=request.form.get("title","").strip(); priority=request.form.get("priority","medium")
        if title and priority in ("low","medium","high"):
            get_db().execute("UPDATE tasks SET title=?,description=?,priority=?,due_date=? WHERE id=?",(title,request.form.get("description","").strip(),priority,parse_date(request.form.get("due_date")),item_id));get_db().commit();flash("Task updated.","success");return redirect(url_for("tasks"))
        flash("Please enter a title and valid priority.","error")
    return render_template("task_edit.html", task=task, user=current_user())

@app.post("/tasks/<int:item_id>/delete")
@login_required
def delete_task(item_id):
    if owned("tasks",item_id): get_db().execute("DELETE FROM tasks WHERE id=?",(item_id,));get_db().commit();flash("Task deleted.","success")
    return redirect(url_for("tasks"))

@app.route("/habits", methods=["GET","POST"])
@login_required
def habits():
    if request.method=="POST":
        name=request.form.get("name","").strip()
        if name: get_db().execute("INSERT INTO habits(user_id,name,description) VALUES(?,?,?)",(session["user_id"],name,request.form.get("description","").strip()));get_db().commit();flash("Habit added.","success")
        else: flash("Habit name is required.","error")
        return redirect(url_for("habits"))
    rows=data_context()[1]; db=get_db()
    enriched=[]
    for h in rows:
        checks={r["check_date"] for r in db.execute("SELECT check_date FROM habit_checks WHERE habit_id=?",(h["id"],))}; streak=0; cursor=date.today()
        while cursor.isoformat() in checks: streak+=1;cursor-=timedelta(days=1)
        enriched.append((h,streak))
    return render_template("habits.html", habits=enriched, user=current_user())

@app.post("/habits/<int:item_id>/check")
@login_required
def check_habit(item_id):
    if not owned("habits",item_id): return ("Not found",404)
    db=get_db(); today=date.today().isoformat(); exists=db.execute("SELECT id FROM habit_checks WHERE habit_id=? AND check_date=?",(item_id,today)).fetchone()
    if exists: db.execute("DELETE FROM habit_checks WHERE id=?",(exists["id"],))
    else: db.execute("INSERT INTO habit_checks(habit_id,check_date) VALUES(?,?)",(item_id,today))
    db.commit();return redirect(url_for("habits"))

@app.post("/habits/<int:item_id>/delete")
@login_required
def delete_habit(item_id):
    if owned("habits",item_id):
        db=get_db();db.execute("DELETE FROM habit_checks WHERE habit_id=?",(item_id,));db.execute("DELETE FROM habits WHERE id=?",(item_id,));db.commit();flash("Habit deleted.","success")
    return redirect(url_for("habits"))

@app.route("/goals", methods=["GET","POST"])
@login_required
def goals():
    if request.method=="POST":
        title=request.form.get("title","").strip()
        if title: get_db().execute("INSERT INTO goals(user_id,title,description,target_date) VALUES(?,?,?,?)",(session["user_id"],title,request.form.get("description","").strip(),parse_date(request.form.get("target_date"))));get_db().commit();flash("Goal added.","success")
        else: flash("Goal title is required.","error")
        return redirect(url_for("goals"))
    return render_template("goals.html", goals=data_context()[2], user=current_user())

@app.post("/goals/<int:item_id>/progress")
@login_required
def goal_progress(item_id):
    if not owned("goals",item_id): return ("Not found",404)
    try: progress=max(0,min(100,int(request.form.get("progress",0))))
    except ValueError: progress=0
    get_db().execute("UPDATE goals SET progress=? WHERE id=?",(progress,item_id));get_db().commit();return redirect(url_for("goals"))

@app.post("/goals/<int:item_id>/delete")
@login_required
def delete_goal(item_id):
    if owned("goals",item_id): get_db().execute("DELETE FROM goals WHERE id=?",(item_id,));get_db().commit();flash("Goal deleted.","success")
    return redirect(url_for("goals"))

def user_summary():
    tasks,habits,goals=data_context(); return {"tasks": [{"title":t["title"],"priority":t["priority"],"due":t["due_date"],"done":bool(t["completed"])} for t in tasks], "habits":[h["name"] for h in habits], "goals":[{"title":g["title"],"progress":g["progress"],"target":g["target_date"]} for g in goals]}

class AIConfigurationError(RuntimeError):
    pass

class AIServiceError(RuntimeError):
    pass

def ask_ai(prompt):
    key = os.getenv("GEMINI_API_KEY", "").strip()
    model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip() or "gemini-3.8-flash"
    if not key:
        raise AIConfigurationError("Gemini is not configured. Add GEMINI_API_KEY to .env, then restart the app.")
    system = (
        "You are AI Life Assistant, a helpful and concise productivity coach. "
        "Reply in the same language as the user: English, Hindi, or Hinglish. "
        "Help with daily planning, task management, habit tracking, goal setting, "
        "productivity, and progress analysis. Use only the supplied private data and "
        "never claim that you performed actions in the app."
    )
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise AIConfigurationError("Gemini support is not installed. Run: python -m pip install -r requirements.txt") from exc
    try:
        client = genai.Client(api_key=key)
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(system_instruction=system, temperature=0.7),
        )
        answer = (response.text or "").strip()
        if not answer:
            raise AIServiceError("Gemini returned an empty response. Please try again.")
        return answer
    except AIConfigurationError:
        raise
    except AIServiceError:
        raise
    except Exception as exc:
        status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
        if status in (401, 403):
            raise AIConfigurationError("Gemini rejected GEMINI_API_KEY. Check the key in .env and restart the app.") from exc
        if status == 429:
            raise AIServiceError("Gemini quota is currently exhausted. Please wait and try again later.") from exc
        if status == 404:
            raise AIConfigurationError("Gemini model is unavailable to this API key. Check GEMINI_MODEL in .env and restart the app.") from exc
        if status == 400:
            raise AIConfigurationError("Gemini configuration is invalid. Check GEMINI_MODEL in .env and restart the app.") from exc
        raise AIServiceError("Gemini could not complete the request. Check your network connection and try again.") from exc

@app.route("/ai")
@login_required
def ai_page():
    messages=get_db().execute("SELECT role,content,created_at FROM chat_messages WHERE user_id=? ORDER BY id DESC LIMIT 30",(session["user_id"],)).fetchall()
    return render_template("ai.html", messages=reversed(messages), user=current_user())

@app.post("/api/ai")
@login_required
def ai_api():
    body=request.get_json(silent=True) or {}; message=body.get("message","").strip(); mode=body.get("mode","chat")
    if mode not in ("chat","daily_plan","weekly_analysis","suggestions"): return jsonify(error="Unknown AI action."),400
    if mode=="chat" and not message: return jsonify(error="Please write a message."),400
    context=json.dumps(user_summary(),ensure_ascii=False)
    prompts={"daily_plan":"Create a realistic prioritized daily plan using this saved data. Include time blocks only as flexible suggestions.","weekly_analysis":"Analyze this saved data for weekly progress. Mention wins, blockers, and 3 practical next steps.","suggestions":"Give 4 personalized productivity suggestions based on this actual saved data."}
    query=message if mode=="chat" else prompts[mode]
    full=f"User data: {context}\n\nRequest: {query}"
    try: answer=ask_ai(full)
    except (AIConfigurationError, AIServiceError) as exc: return jsonify(error=str(exc)),503
    db=get_db();db.execute("INSERT INTO chat_messages(user_id,role,content) VALUES(?,?,?)",(session["user_id"],"user",query));db.execute("INSERT INTO chat_messages(user_id,role,content) VALUES(?,?,?)",(session["user_id"],"assistant",answer));db.commit()
    return jsonify(answer=answer)

if __name__ == "__main__":
    with app.app_context(): init_db()
    app.run(debug=True)
