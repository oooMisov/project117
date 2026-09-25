import os

# База данных будет создаваться в памяти /tmp
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:////tmp/database.db'
from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from sqlalchemy import func

app = Flask(__name__)

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///budget.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    amount = db.Column(db.Float, nullable=False)
    type = db.Column(db.String(10), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    comment = db.Column(db.String(200))
    date = db.Column(db.DateTime, default=datetime.now)


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

    return render_template(
        "index.html",
        transactions=transactions,
        income=income,
        expenses=expenses,
        balance=balance
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


@app.route("/delete/<int:id>", methods=["POST"])
def delete_transaction(id):
    transaction = db.get_or_404(Transaction, id)

    db.session.delete(transaction)
    db.session.commit()

    return redirect(url_for("index"))


if __name__ == "__main__":
    with app.app_context():
        db.create_all()

    app.run(debug=True)
