import hmac
import os
import re
import secrets
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps

from flask import Flask, flash, redirect, render_template, request, session, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool
from werkzeug.security import check_password_hash, generate_password_hash


app = Flask(__name__)

secret_key = os.environ.get("SECRET_KEY")
if not secret_key:
    raise RuntimeError("SECRET_KEY is not set. Add a strong random SECRET_KEY to environment variables.")

app.config.update(
    SECRET_KEY=secret_key,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "1") == "1",
    MAX_CONTENT_LENGTH=16 * 1024,
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
)

database_url = os.environ.get("DATABASE_URL")
if not database_url:
    raise RuntimeError("DATABASE_URL is not set. Add your PostgreSQL connection string to environment variables.")

if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"poolclass": NullPool}

db = SQLAlchemy(app)


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    return response

ALLOWED_TYPES = {"income", "expense"}
ALLOWED_CATEGORIES = {
    "Еда",
    "Кэшбек",
    "Налоги",
    "Подарок",
    "Транспорт",
    "Развлечения",
    "Учёба",
    "Зарплата",
    "Другое",
}
USERNAME_RE = re.compile(r"^[A-Za-zА-Яа-яЁё0-9_.-]{3,50}$")


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)


class Transaction(db.Model):
    __tablename__ = "transaction"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    type = db.Column(db.String(10), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    comment = db.Column(db.String(200))
    date = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped_view


def get_current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return db.session.get(User, user_id)


@app.context_processor
def inject_user():
    return {"current_user": get_current_user()}


def get_csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


@app.context_processor
def inject_csrf_token():
    return {"csrf_token": get_csrf_token}


def validate_csrf():
    token = request.form.get("csrf_token", "")
    expected = session.get("csrf_token", "")
    if not expected or not hmac.compare_digest(token, expected):
        return False
    return True


def parse_amount(raw_amount):
    try:
        amount = Decimal(raw_amount.replace(",", ".").strip())
    except (AttributeError, InvalidOperation):
        return None

    if not amount.is_finite() or amount <= 0 or amount > Decimal("9999999999.99"):
        return None

    return amount.quantize(Decimal("0.01"))


def parse_transaction_date(raw_date):
    try:
        selected = date.fromisoformat(raw_date) if raw_date else date.today()
    except ValueError:
        return None

    # Не разрешаем создавать операции с датой более чем на год вперёд.
    if selected > date.today() + timedelta(days=366):
        return None

    return datetime.combine(selected, datetime.min.time())


def migrate_database():
    """Small compatibility migration for installations made with older versions."""
    inspector = inspect(db.engine)
    tables = set(inspector.get_table_names())

    if "users" not in tables:
        return

    if "transaction" not in tables:
        return

    columns = {column["name"]: column for column in inspector.get_columns("transaction")}

    if "user_id" not in columns:
        db.session.execute(
            text('ALTER TABLE "transaction" ADD COLUMN user_id INTEGER REFERENCES users(id)')
        )
        db.session.commit()

    # Existing legacy installations used FLOAT. PostgreSQL can safely convert
    # existing numeric values to NUMERIC(12,2), while the model now uses Decimal.
    amount_type = str(columns.get("amount", {}).get("type", "")).upper()
    if "DOUBLE" in amount_type or "REAL" in amount_type or "FLOAT" in amount_type:
        db.session.execute(
            text(
                'ALTER TABLE "transaction" '
                'ALTER COLUMN amount TYPE NUMERIC(12,2) '
                'USING ROUND(amount::numeric, 2)'
            )
        )
        db.session.commit()

    # Old rows may have NULL user_id. They cannot safely be assigned automatically,
    # so they remain inaccessible to authenticated users rather than leaking data.


@app.route("/register", methods=["GET", "POST"])
def register():
    if "user_id" in session:
        return redirect(url_for("index"))

    error = None
    username = ""

    if request.method == "POST":
        if not validate_csrf():
            return "Недействительный запрос. Обновите страницу и попробуйте снова.", 400

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not USERNAME_RE.fullmatch(username):
            error = "Логин: 3–50 символов, только буквы, цифры, точка, дефис и подчёркивание."
        elif len(password) < 8:
            error = "Пароль должен содержать минимум 8 символов."
        elif len(password) > 128:
            error = "Пароль слишком длинный."
        elif User.query.filter_by(username=username).first():
            error = "Пользователь с таким логином уже существует."
        else:
            user = User(username=username, password_hash=generate_password_hash(password))
            db.session.add(user)
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                error = "Пользователь с таким логином уже существует."
            else:
                session.clear()
                session["user_id"] = user.id
                session["csrf_token"] = secrets.token_urlsafe(32)
                return redirect(url_for("index"))

    return render_template("register.html", error=error, username=username)


@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("index"))

    error = None
    username = ""

    if request.method == "POST":
        if not validate_csrf():
            return "Недействительный запрос. Обновите страницу и попробуйте снова.", 400

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()

        if user and check_password_hash(user.password_hash, password):
            session.clear()
            session["user_id"] = user.id
            session["csrf_token"] = secrets.token_urlsafe(32)
            return redirect(url_for("index"))

        error = "Неверный логин или пароль."

    return render_template("login.html", error=error, username=username)


@app.post("/logout")
@login_required
def logout():
    if not validate_csrf():
        return "Недействительный запрос. Обновите страницу и попробуйте снова.", 400
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    user_id = session["user_id"]
    transactions = (
        Transaction.query.filter_by(user_id=user_id).order_by(Transaction.date.desc(), Transaction.id.desc()).all()
    )

    income = db.session.query(func.coalesce(func.sum(Transaction.amount), 0)).filter(
        Transaction.user_id == user_id, Transaction.type == "income"
    ).scalar()
    expenses = db.session.query(func.coalesce(func.sum(Transaction.amount), 0)).filter(
        Transaction.user_id == user_id, Transaction.type == "expense"
    ).scalar()
    balance = Decimal(income or 0) - Decimal(expenses or 0)

    return render_template(
        "index.html",
        transactions=transactions,
        income=Decimal(income or 0),
        expenses=Decimal(expenses or 0),
        balance=balance,
    )


@app.route("/add", methods=["GET", "POST"])
@login_required
def add_transaction():
    error = None
    form_data = {
        "amount": "",
        "type": "expense",
        "category": "Другое",
        "comment": "",
        "date": date.today().isoformat(),
    }

    if request.method == "POST":
        if not validate_csrf():
            return "Недействительный запрос. Обновите страницу и попробуйте снова.", 400

        form_data.update({
            "amount": request.form.get("amount", "").strip(),
            "type": request.form.get("type", ""),
            "category": request.form.get("category", ""),
            "comment": request.form.get("comment", "").strip(),
            "date": request.form.get("date", ""),
        })

        amount = parse_amount(form_data["amount"])
        transaction_type = form_data["type"]
        category = form_data["category"]
        comment = form_data["comment"]
        selected_date = parse_transaction_date(form_data["date"])

        if amount is None:
            error = "Введите корректную сумму от 0,01 до 9 999 999 999,99 ₽."
        elif transaction_type not in ALLOWED_TYPES:
            error = "Выберите корректный тип операции."
        elif category not in ALLOWED_CATEGORIES:
            error = "Выберите корректную категорию."
        elif len(comment) > 200:
            error = "Комментарий не должен превышать 200 символов."
        elif selected_date is None:
            error = "Укажите корректную дату."
        else:
            transaction = Transaction(
                user_id=session["user_id"],
                amount=amount,
                type=transaction_type,
                category=category,
                comment=comment or None,
                date=selected_date,
            )
            db.session.add(transaction)
            db.session.commit()
            flash("Операция добавлена.", "success")
            return redirect(url_for("index"))

    return render_template(
        "add.html",
        now=datetime.now(),
        error=error,
        form_data=form_data,
        categories=sorted(ALLOWED_CATEGORIES),
    )


@app.post("/delete/<int:id>")
@login_required
def delete_transaction(id):
    if not validate_csrf():
        return "Недействительный запрос. Обновите страницу и попробуйте снова.", 400

    transaction = Transaction.query.filter_by(id=id, user_id=session["user_id"]).first_or_404()
    db.session.delete(transaction)
    db.session.commit()
    flash("Операция удалена.", "success")
    return redirect(url_for("index"))


with app.app_context():
    db.create_all()
    migrate_database()


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG", "0") == "1")
