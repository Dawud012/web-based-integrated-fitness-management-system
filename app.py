import sqlite3
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from models.db import get_db, init_db
import re

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

def get_db():
    conn = sqlite3.connect("instance/app.db")
    conn.row_factory = sqlite3.Row
    return conn

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        first_name = request.form.get("first_name", "").strip()
        last_name  = request.form.get("last_name", "").strip()
        email      = request.form.get("email", "").strip().lower()
        password   = request.form.get("password", "")

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
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        conn.close()

        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["user_name"] = user["first_name"]
            return redirect(url_for("dashboard"))

        flash("Invalid email or password.", "error")
        return redirect(url_for("login"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("landing"))


@app.route("/dashboard")
def dashboard():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    return render_template("index.html")


if __name__ == "__main__":
    app.run(debug=True)
