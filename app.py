from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from models.db import get_db, init_db
import re
from datetime import date
import os
import requests
import random
from flask_mail import Mail, Message
import secrets
from datetime import datetime, timedelta
from dotenv import load_dotenv
load_dotenv()
import anthropic


def password_is_strong(pw: str):
    if len(pw) < 8:
        return False, "Password must be at least 8 characters."
    if not re.search(r"[A-Z]", pw):
        return False, "Password must include at least 1 uppercase letter."
    if not re.search(r"[a-z]", pw):
        return False, "Password must include at least 1 lowercase letter."
    if not re.search(r"\d", pw):
        return False, "Password must include at least 1 number."
    if not re.search(r"[!@#$%^&*()_\-+=\[\]{};:'\",.<>/?\\|`~]", pw):
        return False, "Password must include at least 1 special character."
    return True, ""

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-fallback-key")

# Email configuration
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME', '')  # Gmail address
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD', '')  # Gmail App Password
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_USERNAME', '')

mail = Mail(app)

# Create tables when app starts
with app.app_context():
    init_db()


@app.route("/")
def landing():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    return render_template("landing.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        # Keep what the user typed (don't keep password for safety)
        form_data = {
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
        }

        # 1) Check all fields filled
        if not first_name or not last_name or not email or not password:
            flash("Please fill in all fields.", "error")
            return render_template("register.html", form=form_data)

        # 2) Password strength check
        ok, msg = password_is_strong(password)
        if not ok:
            flash(msg, "error")
            return render_template("register.html", form=form_data)

        conn = get_db()

        # 3) Email uniqueness check
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            conn.close()
            flash("Email already registered. Please log in.", "error")
            return render_template("register.html", form=form_data)

        # Hash password and create user
        password_hash = generate_password_hash(password)
        conn.execute(
            "INSERT INTO users (first_name, last_name, email, password_hash) VALUES (?, ?, ?, ?)",
            (first_name, last_name, email, password_hash),
        )
        conn.commit()
        conn.close()

        flash("Account created successfully. Please log in.", "success")
        return redirect(url_for("login"))

    # GET request (empty form)
    return render_template("register.html", form={})


@app.route("/login", methods=["GET", "POST"])
def login():
    # If already logged in, go straight to dashboard
    if session.get("user_id"):
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        # Basic validation
        if not email or not password:
            flash("Please enter your email and password.", "error")
            return render_template("login.html", email=email)

        conn = get_db()
        user = conn.execute(
            "SELECT id, first_name, password_hash FROM users WHERE email = ?",
            (email,),
        ).fetchone()
        conn.close()

        # Check user + password
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["user_name"] = user["first_name"]
            flash(f"Welcome back, {user['first_name']}!", "success")
            return redirect(url_for("dashboard"))

        flash("Invalid email or password.", "error")
        return render_template("login.html", email=email)

    return render_template("login.html", email="")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("landing"))


@app.route("/dashboard")
def dashboard():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    
    user_id = session["user_id"]
    conn = get_db()
    
    # Get daily quote
    quotes_list = conn.execute("SELECT * FROM quotes").fetchall()
    daily_quote = None
    if quotes_list:
        today_seed = int(date.today().strftime("%Y%m%d"))
        random.seed(today_seed)
        daily_quote = random.choice(quotes_list)
        random.seed()
    
    # Get recent activity (last 10 items)
    recent_workouts = conn.execute("""
        SELECT 'workout' as type, title as name, workout_date as activity_date
        FROM workout_sessions
        WHERE user_id = ?
        ORDER BY created_at DESC
        LIMIT 5
    """, (user_id,)).fetchall()
    
    recent_meals = conn.execute("""
        SELECT 'diet' as type, food_name as name, entry_date as activity_date
        FROM diet_entries
        WHERE user_id = ?
        ORDER BY created_at DESC
        LIMIT 5
    """, (user_id,)).fetchall()
    
    recent_goals = conn.execute("""
        SELECT 'goal' as type, title as name, created_at as activity_date
        FROM goals
        WHERE user_id = ?
        ORDER BY created_at DESC
        LIMIT 5
    """, (user_id,)).fetchall()
    
    conn.close()
    
    # Combine and sort by date
    all_activity = list(recent_workouts) + list(recent_meals) + list(recent_goals)
    
    # Sort by activity_date descending (most recent first)
    all_activity.sort(key=lambda x: x["activity_date"], reverse=True)
    
    # Take only the 5 most recent
    recent_activity = all_activity[:5]
    
    return render_template(
        "index.html",
        daily_quote=daily_quote,
        recent_activity=recent_activity
    )



# Workouts (Sessions + Exercises)


@app.route("/workouts", methods=["GET", "POST"])
def workouts():
    if not session.get("user_id"):
        return redirect(url_for("login"))

    user_id = session["user_id"]

    # Create a new workout session
    if request.method == "POST":
        title = request.form.get("title", "").strip() or "Workout"
        workout_date = request.form.get("workout_date", "").strip() or date.today().isoformat()
        notes = request.form.get("notes", "").strip()

        conn = get_db()
        cur = conn.execute(
            "INSERT INTO workout_sessions (user_id, title, workout_date, notes) VALUES (?, ?, ?, ?)",
            (user_id, title, workout_date, notes),
        )
        session_id = cur.lastrowid
        conn.commit()
        conn.close()

        flash("Workout created. Now add exercises.", "success")
        return redirect(url_for("workout_detail", session_id=session_id))

    # List sessions (with totals)
    conn = get_db()
    rows = conn.execute("""
        SELECT
            ws.id,
            ws.title,
            ws.workout_date,
            ws.notes,
            COUNT(we.id) AS exercise_count,
            COALESCE(SUM(we.duration_minutes), 0) AS total_minutes
        FROM workout_sessions ws
        LEFT JOIN workout_exercises we ON we.session_id = ws.id
        WHERE ws.user_id = ?
        GROUP BY ws.id
        ORDER BY ws.workout_date DESC, ws.id DESC
    """, (user_id,)).fetchall()
    conn.close()

    return render_template("workouts.html", workouts=rows)


@app.route("/workouts/<int:session_id>", methods=["GET", "POST"])
def workout_detail(session_id):
    if not session.get("user_id"):
        return redirect(url_for("login"))

    user_id = session["user_id"]

    # Add an exercise to this session
    if request.method == "POST":
        exercise = request.form.get("exercise", "").strip()
        sets = request.form.get("sets", "").strip()
        reps = request.form.get("reps", "").strip()
        duration_minutes = request.form.get("duration_minutes", "").strip()
        notes = request.form.get("notes", "").strip()

        if not exercise or not sets or not reps or not duration_minutes:
            flash("Please fill in exercise, sets, reps, and duration.", "error")
            return redirect(url_for("workout_detail", session_id=session_id))

        try:
            sets = int(sets)
            reps = int(reps)
            duration_minutes = int(duration_minutes)
        except ValueError:
            flash("Sets, reps, and duration must be numbers.", "error")
            return redirect(url_for("workout_detail", session_id=session_id))

        conn = get_db()

        # Make sure the session belongs to the logged-in user
        session_row = conn.execute(
            "SELECT id FROM workout_sessions WHERE id = ? AND user_id = ?",
            (session_id, user_id),
        ).fetchone()

        if not session_row:
            conn.close()
            flash("Workout not found.", "error")
            return redirect(url_for("workouts"))

        conn.execute("""
            INSERT INTO workout_exercises (session_id, exercise, sets, reps, duration_minutes, notes)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (session_id, exercise, sets, reps, duration_minutes, notes))

        conn.commit()
        conn.close()

        flash("Exercise added ✅", "success")
        return redirect(url_for("workout_detail", session_id=session_id))

    # GET: show session + exercises
    conn = get_db()
    workout = conn.execute(
        "SELECT * FROM workout_sessions WHERE id = ? AND user_id = ?",
        (session_id, user_id),
    ).fetchone()

    if not workout:
        conn.close()
        flash("Workout not found.", "error")
        return redirect(url_for("workouts"))

    exercises = conn.execute(
        "SELECT * FROM workout_exercises WHERE session_id = ? ORDER BY id DESC",
        (session_id,),
    ).fetchall()
    conn.close()

    return render_template("workout_detail.html", workout=workout, exercises=exercises)


@app.route("/workouts/<int:session_id>/edit", methods=["GET", "POST"])
def edit_workout(session_id):
    if not session.get("user_id"):
        return redirect(url_for("login"))

    user_id = session["user_id"]

    conn = get_db()
    workout = conn.execute(
        "SELECT * FROM workout_sessions WHERE id = ? AND user_id = ?",
        (session_id, user_id),
    ).fetchone()

    if not workout:
        conn.close()
        flash("Workout not found.", "error")
        return redirect(url_for("workouts"))

    if request.method == "POST":
        title = request.form.get("title", "").strip() or "Workout"
        workout_date = request.form.get("workout_date", "").strip() or workout["workout_date"]
        notes = request.form.get("notes", "").strip()

        conn.execute(
            "UPDATE workout_sessions SET title = ?, workout_date = ?, notes = ? WHERE id = ? AND user_id = ?",
            (title, workout_date, notes, session_id, user_id),
        )
        conn.commit()
        conn.close()

        flash("Workout updated ✅", "success")
        return redirect(url_for("workout_detail", session_id=session_id))

    conn.close()
    return render_template("workout_edit.html", workout=workout)


@app.route("/workouts/<int:session_id>/delete", methods=["POST"])
def delete_workout_session(session_id):
    if not session.get("user_id"):
        return redirect(url_for("login"))

    user_id = session["user_id"]

    conn = get_db()
    conn.execute(
        "DELETE FROM workout_sessions WHERE id = ? AND user_id = ?",
        (session_id, user_id),
    )
    conn.commit()
    conn.close()

    flash("Workout deleted.", "success")
    return redirect(url_for("workouts"))


@app.route("/workouts/<int:session_id>/exercises/<int:exercise_id>/delete", methods=["POST"])
def delete_exercise(session_id, exercise_id):
    if not session.get("user_id"):
        return redirect(url_for("login"))

    user_id = session["user_id"]

    conn = get_db()

    # ensure session belongs to user
    session_row = conn.execute(
        "SELECT id FROM workout_sessions WHERE id = ? AND user_id = ?",
        (session_id, user_id),
    ).fetchone()

    if not session_row:
        conn.close()
        flash("Workout not found.", "error")
        return redirect(url_for("workouts"))

    conn.execute(
        "DELETE FROM workout_exercises WHERE id = ? AND session_id = ?",
        (exercise_id, session_id),
    )
    conn.commit()
    conn.close()

    flash("Exercise deleted.", "success")
    return redirect(url_for("workout_detail", session_id=session_id))




# Diet Tracker


USDA_API_KEY = os.getenv("USDA_API_KEY", "")


@app.route("/api/food-search")
def food_search():
    if not session.get("user_id"):
        return {"error": "Unauthorized"}, 401

    q = request.args.get("q", "").strip()
    if not q:
        return {"foods": []}

    if not USDA_API_KEY:
        return {"error": "Missing USDA_API_KEY env var"}, 500

    url = "https://api.nal.usda.gov/fdc/v1/foods/search"
    params = {
        "api_key": USDA_API_KEY,
        "query": q,
        "pageSize": 8,
    }

    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()

    foods = []
    for item in data.get("foods", []):
        foods.append({
            "fdcId": item.get("fdcId"),
            "description": item.get("description", ""),
            "brandName": item.get("brandName", ""),
            "foodCategory": item.get("foodCategory", ""),
        })

    return {"foods": foods}


def _pick_nutrients(food_json: dict):
    """
    Returns calories/protein/carbs/fat per 100g if available.
    USDA provides nutrients in multiple ways depending on the item.
    This tries to map common nutrients from foodNutrients.
    """
    calories = protein = carbs = fat = None

    # foodNutrients is a list of objects with nutrient info
    for fn in food_json.get("foodNutrients", []):
        nut = fn.get("nutrient", {}) or {}
        name = (nut.get("name") or "").lower()

        # amount is usually per 100g for many items
        amt = fn.get("amount", None)

        if amt is None:
            continue

        if "energy" in name and calories is None:
            calories = amt
        elif "protein" in name and protein is None:
            protein = amt
        elif ("carbohydrate" in name or "carbs" in name) and carbs is None:
            carbs = amt
        elif "total lipid" in name or "fat" in name:
            if fat is None:
                fat = amt

    return {
        "calories": calories,
        "protein": protein,
        "carbs": carbs,
        "fat": fat,
    }


@app.route("/api/food-detail/<int:fdc_id>")
def food_detail(fdc_id):
    if not session.get("user_id"):
        return {"error": "Unauthorized"}, 401

    if not USDA_API_KEY:
        return {"error": "Missing USDA_API_KEY env var"}, 500

    url = f"https://api.nal.usda.gov/fdc/v1/food/{fdc_id}"
    params = {"api_key": USDA_API_KEY}

    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    food = r.json()

    nutrients = _pick_nutrients(food)

    return {
        "fdcId": fdc_id,
        "description": food.get("description", ""),
        "brandOwner": food.get("brandOwner", ""),
        "nutrients_per_100g": nutrients
    }


@app.route("/diet")
def diet():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    return render_template("diet.html")


@app.route("/diet/save", methods=["POST"])
def diet_save():
    if not session.get("user_id"):
        return redirect(url_for("login"))

    user_id = session["user_id"]

    entry_date = request.form.get("entry_date", "").strip()
    food_name = request.form.get("food_name", "").strip()
    grams = request.form.get("grams", "").strip()
    calories = request.form.get("calories", "").strip()
    protein = request.form.get("protein", "").strip()
    carbs = request.form.get("carbs", "").strip()
    fat = request.form.get("fat", "").strip()

    # basic validation
    if not entry_date or not food_name or not grams:
        flash("Please select a food and fill date + grams.", "error")
        return redirect(url_for("diet"))

    try:
        grams = float(grams)
        calories = float(calories) if calories else None
        protein = float(protein) if protein else None
        carbs = float(carbs) if carbs else None
        fat = float(fat) if fat else None
    except ValueError:
        flash("Numbers invalid (grams/macros).", "error")
        return redirect(url_for("diet"))

    conn = get_db()
    conn.execute("""
        INSERT INTO diet_entries (user_id, entry_date, food_name, grams, calories, protein, carbs, fat)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, entry_date, food_name, grams, calories, protein, carbs, fat))
    conn.commit()
    conn.close()

    flash("Diet entry saved ✅", "success")
    return redirect(url_for("diet"))


@app.route("/diet/history")
def diet_history():
    if not session.get("user_id"):
        return redirect(url_for("login"))

    user_id = session["user_id"]

    conn = get_db()

    # Get all entries grouped by date with daily totals
    daily_totals = conn.execute("""
        SELECT 
            entry_date,
            COUNT(*) as entry_count,
            ROUND(SUM(calories), 1) as total_calories,
            ROUND(SUM(protein), 1) as total_protein,
            ROUND(SUM(carbs), 1) as total_carbs,
            ROUND(SUM(fat), 1) as total_fat
        FROM diet_entries
        WHERE user_id = ?
        GROUP BY entry_date
        ORDER BY entry_date DESC
    """, (user_id,)).fetchall()

    # Get all individual entries (for expandable view)
    all_entries = conn.execute("""
        SELECT *
        FROM diet_entries
        WHERE user_id = ?
        ORDER BY entry_date DESC, id DESC
    """, (user_id,)).fetchall()

    conn.close()

    # Group entries by date for the template
    entries_by_date = {}
    for entry in all_entries:
        date_key = entry["entry_date"]
        if date_key not in entries_by_date:
            entries_by_date[date_key] = []
        entries_by_date[date_key].append(entry)

    return render_template(
        "diet_history.html",
        daily_totals=daily_totals,
        entries_by_date=entries_by_date
    )


@app.route("/diet/delete/<int:entry_id>", methods=["POST"])
def delete_diet_entry(entry_id):
    if not session.get("user_id"):
        return redirect(url_for("login"))

    user_id = session["user_id"]

    conn = get_db()
    conn.execute(
        "DELETE FROM diet_entries WHERE id = ? AND user_id = ?",
        (entry_id, user_id)
    )
    conn.commit()
    conn.close()

    flash("Diet entry deleted.", "success")
    return redirect(url_for("diet_history"))



# Quotes


@app.route("/quotes")
def quotes():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    
    conn = get_db()
    all_quotes = conn.execute("SELECT * FROM quotes ORDER BY id DESC").fetchall()
    conn.close()
    
    return render_template("quotes.html", quotes=all_quotes)


@app.route("/quotes/add", methods=["POST"])
def add_quote():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    
    quote_text = request.form.get("quote_text", "").strip()
    author = request.form.get("author", "").strip() or "Unknown"
    
    if not quote_text:
        flash("Please enter a quote.", "error")
        return redirect(url_for("quotes"))
    
    conn = get_db()
    conn.execute(
        "INSERT INTO quotes (quote_text, author) VALUES (?, ?)",
        (quote_text, author)
    )
    conn.commit()
    conn.close()
    
    flash("Quote added ✅", "success")
    return redirect(url_for("quotes"))


@app.route("/quotes/delete/<int:quote_id>", methods=["POST"])
def delete_quote(quote_id):
    if not session.get("user_id"):
        return redirect(url_for("login"))
    
    conn = get_db()
    conn.execute("DELETE FROM quotes WHERE id = ?", (quote_id,))
    conn.commit()
    conn.close()
    
    flash("Quote deleted.", "success")
    return redirect(url_for("quotes"))



# Goals


@app.route("/goals")
def goals():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    
    user_id = session["user_id"]
    
    conn = get_db()
    
    # Get active goals
    active_goals = conn.execute("""
        SELECT * FROM goals 
        WHERE user_id = ? AND status = 'active'
        ORDER BY created_at DESC
    """, (user_id,)).fetchall()
    
    # Get completed goals
    completed_goals = conn.execute("""
        SELECT * FROM goals 
        WHERE user_id = ? AND status = 'completed'
        ORDER BY created_at DESC
    """, (user_id,)).fetchall()
    
    conn.close()
    
    return render_template("goals.html", active_goals=active_goals, completed_goals=completed_goals)


@app.route("/goals/add", methods=["POST"])
def add_goal():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    
    user_id = session["user_id"]
    
    goal_type = request.form.get("goal_type", "").strip()
    title = request.form.get("title", "").strip()
    target_value = request.form.get("target_value", "").strip()
    unit = request.form.get("unit", "").strip()
    target_date = request.form.get("target_date", "").strip()
    notes = request.form.get("notes", "").strip()
    
    if not title:
        flash("Please enter a goal title.", "error")
        return redirect(url_for("goals"))
    
    try:
        target_value = float(target_value) if target_value else None
    except ValueError:
        flash("Target value must be a number.", "error")
        return redirect(url_for("goals"))
    
    conn = get_db()
    conn.execute("""
        INSERT INTO goals (user_id, goal_type, title, target_value, unit, target_date, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (user_id, goal_type, title, target_value, unit, target_date or None, notes))
    conn.commit()
    conn.close()
    
    flash("Goal created ✅", "success")
    return redirect(url_for("goals"))


@app.route("/goals/<int:goal_id>/update", methods=["POST"])
def update_goal_progress(goal_id):
    if not session.get("user_id"):
        return redirect(url_for("login"))
    
    user_id = session["user_id"]
    current_value = request.form.get("current_value", "").strip()
    
    try:
        current_value = float(current_value) if current_value else 0
    except ValueError:
        flash("Progress value must be a number.", "error")
        return redirect(url_for("goals"))
    
    conn = get_db()
    
    # Get the goal to check if completed
    goal = conn.execute(
        "SELECT * FROM goals WHERE id = ? AND user_id = ?",
        (goal_id, user_id)
    ).fetchone()
    
    if not goal:
        conn.close()
        flash("Goal not found.", "error")
        return redirect(url_for("goals"))
    
    # Check if goal is now completed
    status = "active"
    if goal["target_value"] and current_value >= goal["target_value"]:
        status = "completed"
    
    conn.execute("""
        UPDATE goals 
        SET current_value = ?, status = ?
        WHERE id = ? AND user_id = ?
    """, (current_value, status, goal_id, user_id))
    conn.commit()
    conn.close()
    
    if status == "completed":
        flash("🎉 Congratulations! Goal completed!", "success")
    else:
        flash("Progress updated ✅", "success")
    
    return redirect(url_for("goals"))


@app.route("/goals/<int:goal_id>/complete", methods=["POST"])
def complete_goal(goal_id):
    if not session.get("user_id"):
        return redirect(url_for("login"))
    
    user_id = session["user_id"]
    
    conn = get_db()
    conn.execute("""
        UPDATE goals 
        SET status = 'completed'
        WHERE id = ? AND user_id = ?
    """, (goal_id, user_id))
    conn.commit()
    conn.close()
    
    flash("🎉 Goal marked as complete!", "success")
    return redirect(url_for("goals"))


@app.route("/goals/<int:goal_id>/reactivate", methods=["POST"])
def reactivate_goal(goal_id):
    if not session.get("user_id"):
        return redirect(url_for("login"))
    
    user_id = session["user_id"]
    
    conn = get_db()
    conn.execute("""
        UPDATE goals 
        SET status = 'active'
        WHERE id = ? AND user_id = ?
    """, (goal_id, user_id))
    conn.commit()
    conn.close()
    
    flash("Goal reactivated ✅", "success")
    return redirect(url_for("goals"))


@app.route("/goals/<int:goal_id>/delete", methods=["POST"])
def delete_goal(goal_id):
    if not session.get("user_id"):
        return redirect(url_for("login"))
    
    user_id = session["user_id"]
    
    conn = get_db()
    conn.execute(
        "DELETE FROM goals WHERE id = ? AND user_id = ?",
        (goal_id, user_id)
    )
    conn.commit()
    conn.close()
    
    flash("Goal deleted.", "success")
    return redirect(url_for("goals"))



# Forgot Password (Email-based)


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        
        if not email:
            flash("Please enter your email address.", "error")
            return render_template("forgot_password.html", email="")
        
        conn = get_db()
        user = conn.execute(
            "SELECT id, first_name FROM users WHERE email = ?",
            (email,)
        ).fetchone()
        
        if user:
            # Generate secure token
            token = secrets.token_urlsafe(32)
            expires_at = (datetime.now() + timedelta(hours=1)).isoformat()
            
            # Delete any existing tokens for this user
            conn.execute("DELETE FROM password_reset_tokens WHERE user_id = ?", (user["id"],))
            
            # Save new token
            conn.execute("""
                INSERT INTO password_reset_tokens (user_id, token, expires_at)
                VALUES (?, ?, ?)
            """, (user["id"], token, expires_at))
            conn.commit()
            
            # Create reset link
            reset_link = url_for('reset_password_token', token=token, _external=True)
            
            # Send email
            try:
                msg = Message(
                    subject="Reset Your Password - Fitness Web App",
                    recipients=[email]
                )
                msg.html = f"""
                <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
                    <h2 style="color: #6aa7ff;">Fitness Web App</h2>
                    <p>Hi {user['first_name']},</p>
                    <p>We received a request to reset your password. Click the button below to create a new password:</p>
                    <p style="margin: 25px 0;">
                        <a href="{reset_link}" 
                           style="background: linear-gradient(135deg, #6aa7ff, #3b82f6); 
                                  color: #fff; 
                                  padding: 12px 24px; 
                                  text-decoration: none; 
                                  border-radius: 8px;
                                  display: inline-block;">
                            Reset Password
                        </a>
                    </p>
                    <p style="color: #666; font-size: 14px;">This link will expire in 1 hour.</p>
                    <p style="color: #666; font-size: 14px;">If you didn't request this, you can safely ignore this email.</p>
                    <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">
                    <p style="color: #999; font-size: 12px;">Fitness Web App • Track • Progress • Stay Motivated</p>
                </div>
                """
                mail.send(msg)
                flash("Password reset link sent! Check your email.", "success")
            except Exception as e:
                print(f"Email error: {e}")
                flash("Could not send email. Please try again later.", "error")
        else:
            # Don't reveal if email exists or not (security)
            flash("If an account exists with that email, a reset link has been sent.", "success")
        
        conn.close()
        return render_template("forgot_password.html", email="")
    
    return render_template("forgot_password.html", email="")


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password_token(token):
    conn = get_db()
    
    # Find valid token
    reset_request = conn.execute("""
        SELECT prt.*, u.email 
        FROM password_reset_tokens prt
        JOIN users u ON u.id = prt.user_id
        WHERE prt.token = ? AND prt.used = 0
    """, (token,)).fetchone()
    
    if not reset_request:
        conn.close()
        flash("Invalid or expired reset link. Please request a new one.", "error")
        return redirect(url_for("forgot_password"))
    
    # Check if expired
    expires_at = datetime.fromisoformat(reset_request["expires_at"])
    if datetime.now() > expires_at:
        conn.close()
        flash("This reset link has expired. Please request a new one.", "error")
        return redirect(url_for("forgot_password"))
    
    if request.method == "POST":
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        
        # Validate passwords match
        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return render_template("reset_password.html", token=token)
        
        # Validate password strength
        ok, msg = password_is_strong(password)
        if not ok:
            flash(msg, "error")
            return render_template("reset_password.html", token=token)
        
        # Update password
        password_hash = generate_password_hash(password)
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (password_hash, reset_request["user_id"])
        )
        
        # Mark token as used
        conn.execute(
            "UPDATE password_reset_tokens SET used = 1 WHERE token = ?",
            (token,)
        )
        conn.commit()
        conn.close()
        
        flash("Password reset successful! Please log in with your new password.", "success")
        return redirect(url_for("login"))
    
    conn.close()
    return render_template("reset_password.html", token=token)



# Progress Dashboard


@app.route("/progress")
def progress():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    
    user_id = session["user_id"]
    conn = get_db()
    
    # Get workout stats (last 30 days)
    workout_stats = conn.execute("""
        SELECT 
            ws.workout_date,
            COUNT(DISTINCT ws.id) as workout_count,
            COALESCE(SUM(we.duration_minutes), 0) as total_minutes,
            COUNT(we.id) as exercise_count
        FROM workout_sessions ws
        LEFT JOIN workout_exercises we ON we.session_id = ws.id
        WHERE ws.user_id = ?
        AND ws.workout_date >= date('now', '-30 days')
        GROUP BY ws.workout_date
        ORDER BY ws.workout_date ASC
    """, (user_id,)).fetchall()
    
    # Get diet stats (last 30 days)
    diet_stats = conn.execute("""
        SELECT 
            entry_date,
            ROUND(SUM(calories), 1) as total_calories,
            ROUND(SUM(protein), 1) as total_protein,
            ROUND(SUM(carbs), 1) as total_carbs,
            ROUND(SUM(fat), 1) as total_fat
        FROM diet_entries
        WHERE user_id = ?
        AND entry_date >= date('now', '-30 days')
        GROUP BY entry_date
        ORDER BY entry_date ASC
    """, (user_id,)).fetchall()
    
    # Get totals for summary cards
    total_workouts = conn.execute("""
        SELECT COUNT(*) as count FROM workout_sessions WHERE user_id = ?
    """, (user_id,)).fetchone()["count"]
    
    total_exercises = conn.execute("""
        SELECT COUNT(*) as count 
        FROM workout_exercises we
        JOIN workout_sessions ws ON ws.id = we.session_id
        WHERE ws.user_id = ?
    """, (user_id,)).fetchone()["count"]
    
    total_minutes = conn.execute("""
        SELECT COALESCE(SUM(we.duration_minutes), 0) as total
        FROM workout_exercises we
        JOIN workout_sessions ws ON ws.id = we.session_id
        WHERE ws.user_id = ?
    """, (user_id,)).fetchone()["total"]
    
    total_meals = conn.execute("""
        SELECT COUNT(*) as count FROM diet_entries WHERE user_id = ?
    """, (user_id,)).fetchone()["count"]
    
    avg_calories = conn.execute("""
        SELECT ROUND(AVG(daily_cal), 0) as avg
        FROM (
            SELECT SUM(calories) as daily_cal
            FROM diet_entries
            WHERE user_id = ?
            GROUP BY entry_date
        )
    """, (user_id,)).fetchone()["avg"] or 0
    
    # Goals stats
    active_goals = conn.execute("""
        SELECT COUNT(*) as count FROM goals WHERE user_id = ? AND status = 'active'
    """, (user_id,)).fetchone()["count"]
    
    completed_goals = conn.execute("""
        SELECT COUNT(*) as count FROM goals WHERE user_id = ? AND status = 'completed'
    """, (user_id,)).fetchone()["count"]
    
    conn.close()
    
    # Convert to lists for JSON in template
    workout_dates = [row["workout_date"] for row in workout_stats]
    workout_minutes = [row["total_minutes"] for row in workout_stats]
    workout_exercises = [row["exercise_count"] for row in workout_stats]
    
    diet_dates = [row["entry_date"] for row in diet_stats]
    diet_calories = [row["total_calories"] or 0 for row in diet_stats]
    diet_protein = [row["total_protein"] or 0 for row in diet_stats]
    diet_carbs = [row["total_carbs"] or 0 for row in diet_stats]
    diet_fat = [row["total_fat"] or 0 for row in diet_stats]
    
    return render_template(
        "progress.html",
        # Summary stats
        total_workouts=total_workouts,
        total_exercises=total_exercises,
        total_minutes=total_minutes,
        total_meals=total_meals,
        avg_calories=avg_calories,
        active_goals=active_goals,
        completed_goals=completed_goals,
        # Chart data
        workout_dates=workout_dates,
        workout_minutes=workout_minutes,
        workout_exercises=workout_exercises,
        diet_dates=diet_dates,
        diet_calories=diet_calories,
        diet_protein=diet_protein,
        diet_carbs=diet_carbs,
        diet_fat=diet_fat
    )


# AI Fitness Coach


@app.route("/ai-coach", methods=["GET", "POST"])
def ai_coach():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    
    response_text = None
    user_message = ""
    
    if request.method == "POST":
        user_message = request.form.get("message", "").strip()
        
        if user_message:
            try:
                client = anthropic.Anthropic(
                    api_key=os.getenv("ANTHROPIC_API_KEY")
                )
                
                # Get user's goals for context
                user_id = session["user_id"]
                conn = get_db()
                goals = conn.execute(
                    "SELECT title, goal_type FROM goals WHERE user_id = ? AND status = 'active'",
                    (user_id,)
                ).fetchall()
                conn.close()
                
                goals_context = ""
                if goals:
                    goals_list = [f"{g['goal_type']}: {g['title']}" for g in goals]
                    goals_context = f"The user's current fitness goals are: {', '.join(goals_list)}."
                
                system_prompt = f"""You are a helpful AI fitness coach inside a fitness tracking web app. 
You provide advice on workouts, nutrition, and motivation.
Keep responses concise and actionable (under 300 words).
Be encouraging and supportive.
{goals_context}

When creating workout plans:
- Follow the user's exact request for muscle groups and frequency
- Format clearly with days, exercises, sets, and reps
- Use bullet points for exercises

If asked about nutrition, give practical advice.
Do not provide medical advice - recommend consulting a doctor for health concerns."""
                
                message = client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=1024,
                    system=system_prompt,
                    messages=[
                        {"role": "user", "content": user_message}
                    ]
                )
                
                response_text = message.content[0].text
                
            except Exception as e:
                print(f"AI Error: {e}")
                response_text = "Sorry, I couldn't process your request. Please try again."
    
    return render_template("ai_coach.html", response=response_text, user_message=user_message)
if __name__ == "__main__":
    app.run(debug=True)