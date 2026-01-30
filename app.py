from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from models.db import get_db, init_db
import re
from datetime import date
import os
import requests


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
app.secret_key = "dev-key-change-later"  # later move to .env

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

        # 4) Create user
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
    return render_template("index.html")


# =========================
# Workouts (Sessions + Exercises)
# =========================

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

if __name__ == "__main__":
    app.run(debug=True)


