import os
from datetime import datetime

from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func


app = Flask(__name__)

database_url = os.environ.get("DATABASE_URL")

if not database_url:
    raise RuntimeError(
        "DATABASE_URL is not set. Add your PostgreSQL connection string "
        "to Vercel Environment Variables."
    )

if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,
}

db = SQLAlchemy(app)


class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    amount = db.Column(db.Float, nullable=False)
    type = db.Column(db.String(10), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    comment = db.Column(db.String(200))
    date = db.Column(db.DateTime, default=datetime.now)


class Goal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    amount = db.Column(db.Float, nullable=False)


@app.route("/")
def index():
    transactions = Transaction.query.order_by(
        Transaction.date.desc()
    ).all()

    income = db.session.query(
        func.sum(Transaction.amount)
    ).filter_by(type="income").scalar() or 0

    expenses = db.session.query(
        func.sum(Transaction.amount)
    ).filter_by(type="expense").scalar() or 0

    balance = income - expenses

    goal = Goal.query.first()

    saved_amount = max(balance, 0)

    if goal and goal.amount > 0:
        goal_percent = min((saved_amount / goal.amount) * 100, 100)
        goal_remaining = max(goal.amount - saved_amount, 0)
    else:
        goal_percent = 0
        goal_remaining = 0

    return render_template(
        "index.html",
        transactions=transactions,
        income=income,
        expenses=expenses,
        balance=balance,
        goal=goal,
        saved_amount=saved_amount,
        goal_percent=goal_percent,
        goal_remaining=goal_remaining,
    )


@app.route("/add", methods=["GET", "POST"])
def add_transaction():
    if request.method == "POST":
        amount = float(request.form["amount"])
        transaction_type = request.form["type"]
        category = request.form["category"]
        comment = request.form["comment"]

        transaction = Transaction(
            amount=amount,
            type=transaction_type,
            category=category,
            comment=comment
        )

        db.session.add(transaction)
        db.session.commit()

        return redirect(url_for("index"))

    return render_template("add.html")


@app.route("/goal", methods=["POST"])
def save_goal():
    name = request.form["name"].strip()
    amount = float(request.form["amount"])

    if not name or amount <= 0:
        return redirect(url_for("index"))

    goal = Goal.query.first()

    if goal:
        goal.name = name
        goal.amount = amount
    else:
        goal = Goal(name=name, amount=amount)
        db.session.add(goal)

    db.session.commit()

    return redirect(url_for("index"))


@app.route("/delete/<int:id>", methods=["POST"])
def delete_transaction(id):
    transaction = db.get_or_404(Transaction, id)

    db.session.delete(transaction)
    db.session.commit()

    return redirect(url_for("index"))


with app.app_context():
    db.create_all()


if __name__ == "__main__":
    app.run(debug=True)
