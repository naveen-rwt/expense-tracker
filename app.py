import os
from datetime import datetime, date
from decimal import Decimal
from flask import Flask, render_template, request, redirect, url_for, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from flask_login import login_required, current_user
from flask_login import LoginManager, login_user, login_required, logout_user, current_user, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from io import StringIO
import io
import csv

app = Flask(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///expense.db")
SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "dev-secret")

app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = SECRET_KEY

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    description = db.Column(db.String(255), nullable=True)
    spent_on = db.Column(db.Date, nullable=False, default=date.today)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref=db.backref("expenses", lazy=True))

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not email or not password:
            flash("Email and password are required.", "danger")
            return redirect(url_for("register"))
        if User.query.filter_by(email=email).first():
            flash("Email already registered.", "warning")
            return redirect(url_for("register"))
        user = User(email=email)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        flash("Registration successful. Please log in.", "success")
        return redirect(url_for("login"))
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            login_user(user)
            flash("Logged in successfully.", "success")
            return redirect(url_for("dashboard"))
        flash("Invalid credentials.", "danger")
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Logged out.", "info")
    return redirect(url_for("login"))

@app.route("/", methods=["GET"])
@login_required
def dashboard():
    start = request.args.get("start")
    end = request.args.get("end")
    q = Expense.query.filter_by(user_id=current_user.id)
    if start:
        try:
            start_date = datetime.strptime(start, "%Y-%m-%d").date()
            q = q.filter(Expense.spent_on >= start_date)
        except ValueError:
            flash("Invalid start date.", "warning")
    if end:
        try:
            end_date = datetime.strptime(end, "%Y-%m-%d").date()
            q = q.filter(Expense.spent_on <= end_date)
        except ValueError:
            flash("Invalid end date.", "warning")

    expenses = q.order_by(Expense.spent_on.desc()).all()

    by_category = {}
    for e in expenses:
        by_category[e.category] = by_category.get(e.category, Decimal("0.00")) + e.amount

    by_month = {}
    for e in expenses:
        key = e.spent_on.strftime("%Y-%m")
        by_month[key] = by_month.get(key, Decimal("0.00")) + e.amount

    cat_labels = list(by_category.keys())
    cat_values = [float(v) for v in by_category.values()]
    month_labels = sorted(by_month.keys())
    month_values = [float(by_month[m]) for m in month_labels]

    total_spent = float(sum(cat_values)) if cat_values else 0.0

    return render_template("index.html",
                           expenses=expenses,
                           total_spent=total_spent,
                           cat_labels=cat_labels,
                           cat_values=cat_values,
                           month_labels=month_labels,
                           month_values=month_values)

@app.route("/expenses")
@login_required
def list_expenses():
    expenses = Expense.query.filter_by(user_id=current_user.id).order_by(Expense.spent_on.desc()).all()
    return render_template("expenses.html", expenses=expenses)

@app.route("/expense/add", methods=["GET", "POST"])
@login_required
def add_expense():
    if request.method == "POST":
        amount = request.form.get("amount")
        category = request.form.get("category")
        description = request.form.get("description")
        spent_on = request.form.get("spent_on")
        try:
            amount_dec = Decimal(amount)
            if not spent_on:
                spent_date = date.today()
            else:
                spent_date = datetime.strptime(spent_on, "%Y-%m-%d").date()
        except Exception:
            flash("Please provide valid amount/date.", "danger")
            return redirect(url_for("add_expense"))

        e = Expense(user_id=current_user.id, amount=amount_dec, category=category,
                    description=description, spent_on=spent_date)
        db.session.add(e)
        db.session.commit()
        flash("Expense added.", "success")
        return redirect(url_for("dashboard"))
    return render_template("add_expense.html")

@app.route("/expense/delete/<int:expense_id>", methods=["POST"])
@login_required
def delete_expense(expense_id):
    e = Expense.query.filter_by(id=expense_id, user_id=current_user.id).first_or_404()
    db.session.delete(e)
    db.session.commit()
    flash("Expense deleted.", "info")
    return redirect(url_for("list_expenses"))

@app.route("/export/csv")
@login_required
def export_csv():
    q = Expense.query.filter_by(user_id=current_user.id).order_by(Expense.spent_on.desc()).all()
    si = StringIO()
    writer = csv.writer(si)
    writer.writerow(["Amount", "Category", "Description", "Spent On"])
    for e in q:
        writer.writerow([str(e.amount), e.category, e.description or "", e.spent_on.isoformat()])
    output = si.getvalue().encode("utf-8")
    return send_file(
        io.BytesIO(output),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"expenses_{datetime.utcnow().strftime('%Y%m%d')}.csv",        
    )

@app.route("/profile")
@login_required
def profile():
    total = db.session.query(func.sum(Expense.amount)).filter(Expense.user_id==current_user.id).scalar() or 0
    top = (db.session.query(Expense.category, func.sum(Expense.amount).label("total"))
             .filter(Expense.user_id==current_user.id)
             .group_by(Expense.category)
             .order_by(func.sum(Expense.amount).desc()).first())
    top_cat = top[0] if top else None
    recent = Expense.query.filter_by(user_id=current_user.id).order_by(Expense.spent_on.desc()).limit(5).all()
    count = Expense.query.filter_by(user_id=current_user.id).count()
    return render_template("profile.html", total_spent=float(total), top_cat=top_cat, recent=recent, count=count)

    
@app.route("/about")
def about():
    return render_template("about.html")


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)
