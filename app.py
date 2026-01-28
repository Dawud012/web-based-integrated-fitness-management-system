from flask import Flask, render_template, request, redirect, url_for, session



app = Flask(__name__)
app.secret_key = "change_this_later"  # needed for session login
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("landing"))

@app.route("/")
def landing():
    # If already logged in, send to dashboard
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    return render_template("landing.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        # TODO: validate user properly (later with SQLite)
        session["user_id"] = 1
        return redirect(url_for("dashboard"))
    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        # TODO: create user in DB
        return redirect(url_for("login"))
    return render_template("register.html")

@app.route("/dashboard")
def dashboard():
    # Protect dashboard: must be logged in
    if not session.get("user_id"):
        return redirect(url_for("login"))
    return render_template("index.html")  # your current dashboard UI



if __name__ == "__main__":
    app.run(debug=True)
