import json
import os
import re
import secrets
import sqlite3
import math
import hashlib
import hmac
import smtplib
import ssl
import time
from email.message import EmailMessage
from io import BytesIO
from datetime import datetime
from pathlib import Path
from functools import wraps
from hmac import compare_digest

from contextlib import closing

from flask import Flask, abort, flash, g, jsonify, redirect, render_template, request, send_file, send_from_directory, session, url_for
from PIL import Image, UnidentifiedImageError
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from studio_assistant import AssistantError, assistant_response
from print_artwork import MissingPrintAsset, artwork_filename, group_artworks, print_spec, render_print_png

BASE_DIR = Path(__file__).resolve().parent
DATABASE = Path(os.environ.get("ANSUMAN_DATABASE_PATH", BASE_DIR / "database" / "ansuman_graphic.sqlite3")).expanduser().resolve()
UPLOAD_DIR = Path(os.environ.get("ANSUMAN_UPLOAD_DIR", BASE_DIR / "uploads")).expanduser().resolve()
IS_RENDER = os.environ.get("RENDER", "").lower() == "true"
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg"}
MAX_UPLOAD_BYTES = 12 * 1024 * 1024
MAX_READY_UPLOAD_BYTES = 80 * 1024 * 1024
MAX_PHOTOS_PER_PRODUCT = max(1, int(os.environ.get("ANSUMAN_MAX_ARTWORKS_PER_PRODUCT", "24")))
MAX_TOTAL_UPLOAD_BYTES = max(MAX_UPLOAD_BYTES, int(os.environ.get("ANSUMAN_MAX_CREATE_TOTAL_MB", "96")) * 1024 * 1024)
MAX_IMAGE_PIXELS = 120_000_000
MAX_UPLOAD_REQUEST_BYTES = max(MAX_TOTAL_UPLOAD_BYTES, MAX_READY_UPLOAD_BYTES) + 1024 * 1024
PRODUCTS = [
    ("tshirt", "Custom T-Shirts", 599, "Soft cotton. Made yours."),
    ("mug", "Coffee Mugs / Cups", 349, "A little joy in every sip."),
    ("pillow", "Photo Pillows", 799, "Your favourite moments, close."),
    ("banner", "Advertising Banners", 1299, "Make your message impossible to miss."),
    ("frame", "Photo Frames", 499, "Give your memories a place."),
    ("phonecase", "Phone Covers", 449, "Your style, on the everyday."),
    ("poster", "Posters", 299, "Big ideas deserve a wall."),
    ("gift", "Customized Gifts", 699, "Thoughtful, personal, unforgettable."),
]
SIZES = {
    "tshirt": [("S", 0), ("M", 0), ("L", 40), ("XL", 80), ("XXL", 120)],
    "mug": [("11 oz", 0), ("15 oz", 120)],
    "pillow": [("12 × 12 in", 0), ("16 × 16 in", 180), ("18 × 18 in", 280)],
    "banner": [("2 × 3 ft", 0), ("3 × 5 ft", 500), ("4 × 8 ft", 1100)],
    "frame": [("5 × 7 in", 0), ("8 × 10 in", 250), ("12 × 16 in", 600)],
    "phonecase": [("iPhone", 0), ("Samsung", 0), ("Other", 0)],
    "poster": [("A4", 0), ("A3", 150), ("A2", 350)],
    "gift": [("Standard", 0), ("Premium", 300)],
}
PRINTING_CHARGE = 99
PRINT_AREAS = {
    "tshirt": [("front_center", "Front centre"), ("back_center", "Back centre"), ("left_chest", "Left chest"), ("right_chest", "Right chest"), ("left_sleeve", "Left sleeve"), ("right_sleeve", "Right sleeve")],
    "mug": [("wrap", "Wrap print")], "pillow": [("front", "Front panel")],
    "banner": [("full_front", "Full front")], "frame": [("photo", "Photo opening")],
    "phonecase": [("back", "Back panel")], "poster": [("full_front", "Full front")],
    "gift": [("front", "Front panel")],
}

app = Flask(
    __name__,
    static_folder=str(BASE_DIR / "static"),
    static_url_path="/static",
)
app.config["DEBUG"] = False
DATABASE.parent.mkdir(parents=True, exist_ok=True)
secret_key = os.environ.get("FLASK_SECRET_KEY")
if secret_key:
    app.secret_key = secret_key
elif IS_RENDER:
    raise RuntimeError("FLASK_SECRET_KEY must be set in the Render environment.")
else:
    secret_file = DATABASE.parent / ".flask_secret"
    if not secret_file.exists():
        secret_file.write_text(secrets.token_hex(32), encoding="utf-8")
    app.secret_key = secret_file.read_text(encoding="utf-8").strip()
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_REQUEST_BYTES
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("COOKIE_SECURE", "1" if IS_RENDER else "0") == "1"


def development_debug_enabled():
    """Allow the Werkzeug debugger only for explicitly enabled local development."""
    return not IS_RENDER and os.environ.get("FLASK_DEBUG", "").lower() in {"1", "true", "yes"}


def get_csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


@app.context_processor
def inject_security_context():
    return {"csrf_token": get_csrf_token, "customer_logged_in": session.get("role") == "customer",
            "admin_logged_in": session.get("role") == "admin"}


@app.before_request
def csrf_protect():
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return None
    expected = session.get("_csrf_token", "")
    supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
    if not expected or not supplied or not compare_digest(expected, supplied):
        abort(400, "Your form session expired. Refresh the page and try again.")


def connect_db():
    """Return the request-scoped database connection, closed by app teardown."""
    if "database" not in g:
        DATABASE.parent.mkdir(parents=True, exist_ok=True)
        g.database = sqlite3.connect(DATABASE)
        g.database.row_factory = sqlite3.Row
    return g.database


@app.teardown_appcontext
def close_db(_error=None):
    db = g.pop("database", None)
    if db is not None:
        db.close()


def initialize_database():
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(DATABASE)) as db:
        with db:
            db.execute("CREATE TABLE IF NOT EXISTS products (slug TEXT PRIMARY KEY, name TEXT NOT NULL, price REAL NOT NULL, description TEXT NOT NULL)")
            db.execute("""CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT, customer_name TEXT NOT NULL, email TEXT NOT NULL,
                phone TEXT NOT NULL, address TEXT NOT NULL, items_json TEXT NOT NULL,
                total REAL NOT NULL, created_at TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
                phone TEXT NOT NULL, password_hash TEXT NOT NULL, created_at TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS admin_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL, created_at TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS admin_password_resets (
                token_hash TEXT PRIMARY KEY, admin_id INTEGER NOT NULL,
                expires_at INTEGER NOT NULL, used_at INTEGER,
                FOREIGN KEY(admin_id) REFERENCES admin_users(id) ON DELETE CASCADE)""")
            db.execute("""CREATE TABLE IF NOT EXISTS admin_recovery_limits (
                bucket TEXT NOT NULL, key_hash TEXT NOT NULL, window_started INTEGER NOT NULL,
                attempts INTEGER NOT NULL, PRIMARY KEY(bucket,key_hash))""")
            db.execute("CREATE TABLE IF NOT EXISTS categories (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE)")
            db.execute("CREATE TABLE IF NOT EXISTS site_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            product_columns = {row[1] for row in db.execute("PRAGMA table_info(products)")}
            inventory_added = "inventory" not in product_columns
            for name, definition in {
                "category_id": "INTEGER", "enabled": "INTEGER NOT NULL DEFAULT 1", "sizes_json": "TEXT NOT NULL DEFAULT '{}'",
                "colors_json": "TEXT NOT NULL DEFAULT '[]'", "inventory": "INTEGER NOT NULL DEFAULT 0",
                "image": "TEXT NOT NULL DEFAULT ''", "printing_charge": "REAL NOT NULL DEFAULT 99",
                "design_charge": "REAL NOT NULL DEFAULT 0", "discount_percent": "REAL NOT NULL DEFAULT 0",
                "featured": "INTEGER NOT NULL DEFAULT 0", "photo_views_json": "TEXT NOT NULL DEFAULT '{}'",
                "model_file": "TEXT NOT NULL DEFAULT ''", "model_license": "TEXT NOT NULL DEFAULT ''",
                "print_surfaces_json": "TEXT NOT NULL DEFAULT '{}'", "photo_print_areas_json": "TEXT NOT NULL DEFAULT '{}'",
            }.items():
                if name not in product_columns:
                    db.execute(f"ALTER TABLE products ADD COLUMN {name} {definition}")
            order_columns = {row[1] for row in db.execute("PRAGMA table_info(orders)")}
            for name, definition in {
                "customer_id": "INTEGER", "status": "TEXT NOT NULL DEFAULT 'Pending'", "delivery_charge": "REAL NOT NULL DEFAULT 0",
                "delivered_at": "TEXT",
            }.items():
                if name not in order_columns:
                    db.execute(f"ALTER TABLE orders ADD COLUMN {name} {definition}")
            db.execute("""CREATE TABLE IF NOT EXISTS customer_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER NOT NULL,
                order_id INTEGER NOT NULL,
                notification_type TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(customer_id, order_id, notification_type)
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_customer_notifications_customer ON customer_notifications(customer_id, id DESC)")
            db.execute("INSERT OR IGNORE INTO categories (name) VALUES ('Custom Prints')")
            category_id = db.execute("SELECT id FROM categories WHERE name='Custom Prints'").fetchone()[0]
            db.execute("INSERT OR IGNORE INTO site_settings (key,value) VALUES ('printing_charge','99'),('design_charge','0'),('delivery_charge','0'),('hero_title','Make your ideas tangible.'),('hero_subtitle','Premium custom printing, made personal.'),('banner_image','images/storefront-banner-reference.png'),('promo_text','Made for your moments.')")
            for slug, name, price, description in PRODUCTS:
                db.execute("INSERT OR IGNORE INTO products (slug,name,price,description) VALUES (?,?,?,?)", (slug, name, price, description))
            colors = {"tshirt", "mug", "pillow", "phonecase", "gift"}
            for slug, *_ in PRODUCTS:
                db.execute("UPDATE products SET category_id=COALESCE(category_id,?), sizes_json=CASE WHEN sizes_json='{}' OR sizes_json='' THEN ? ELSE sizes_json END, colors_json=CASE WHEN colors_json='[]' OR colors_json='' THEN ? ELSE colors_json END WHERE slug=?",
                           (category_id, json.dumps(dict(SIZES.get(slug, [("Standard", 0)]))), json.dumps(["white", "black", "navy", "red", "blue", "pink", "yellow"] if slug in colors else []), slug))
            if inventory_added:
                db.execute("UPDATE products SET inventory=100")


initialize_database()


def product_rows():
    return connect_db().execute("SELECT * FROM products WHERE enabled=1 ORDER BY rowid").fetchall()


def get_product(slug):
    return connect_db().execute("SELECT * FROM products WHERE slug = ? AND enabled=1", (slug,)).fetchone()


def sizes_for_product(product):
    try:
        sizes = json.loads(product["sizes_json"] or "{}")
        if isinstance(sizes, dict) and sizes:
            return [(str(label), max(0, float(extra))) for label, extra in sizes.items()]
    except (ValueError, TypeError, KeyError):
        pass
    return SIZES.get(product["slug"], [("Standard", 0)])


def settings_map():
    return {row["key"]: row["value"] for row in connect_db().execute("SELECT key,value FROM site_settings")}


def homepage_banners_from_settings(settings):
    """Decode the independently managed homepage promotional banners."""
    try:
        banners = json.loads(settings.get("homepage_banners", "[]"))
    except (TypeError, ValueError):
        return []
    if not isinstance(banners, list):
        return []
    return [banner for banner in banners if isinstance(banner, dict)
            and isinstance(banner.get("id"), str)
            and isinstance(banner.get("image"), str)
            and isinstance(banner.get("title"), str)
            and isinstance(banner.get("url"), str)]


def validate_homepage_banner_url(value):
    value = (value or "").strip()[:500]
    if not value:
        return "/products"
    if value.startswith("/") and not value.startswith("//") and "\\" not in value:
        return value
    from urllib.parse import urlsplit
    parsed = urlsplit(value)
    if parsed.scheme in {"http", "https"} and parsed.netloc and not parsed.username and not parsed.password:
        return value
    raise ValueError("Banner link must be a site path or a valid http/https URL.")


def setting_price(key, fallback=0):
    try:
        return max(0, float(settings_map().get(key, fallback)))
    except (ValueError, TypeError):
        return float(fallback)


def price_item(item, product=None):
    product = product or connect_db().execute("SELECT * FROM products WHERE slug=?", (item.get("slug"),)).fetchone()
    if product is None:
        return item
    sizes = dict(sizes_for_product(product))
    size_extra = sizes.get(item.get("size"), 0)
    base = float(product["price"])
    printing = float(product["printing_charge"] if "printing_charge" in product.keys() else setting_price("printing_charge", PRINTING_CHARGE))
    design = float(product["design_charge"] if "design_charge" in product.keys() else setting_price("design_charge", 0))
    discount_percent = max(0, min(100, float(product["discount_percent"] or 0)))
    undiscounted = base + size_extra + printing + design
    discount = round(undiscounted * discount_percent / 100, 2)
    item.update(base_price=base, size_price=size_extra, printing_charge=printing, design_charge=design,
                discount=discount, discount_percent=discount_percent, unit_price=round(undiscounted-discount, 2))
    return item


def current_cart_items():
    return [price_item(dict(item)) for item in cart_items()]


def cart_items():
    return session.get("cart", [])


def cart_total(items):
    return sum(float(item["unit_price"]) * int(item["quantity"]) for item in items)


def finite_number(value, default=0.0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if math.isfinite(number) else float(default)


def studio_catalog(rows=None):
    rows = rows if rows is not None else product_rows()
    color_slugs = {"tshirt", "mug", "pillow", "phonecase", "gift"}
    return {
        row["slug"]: {
            "slug": row["slug"], "name": row["name"], "description": row["description"],
            "price": float(row["price"]),
            "discount_percent": float(row["discount_percent"] or 0),
            "printing_charge": float(row["printing_charge"]),
            "design_charge": float(row["design_charge"]),
            "sizes": [{"label": label, "extra": extra} for label, extra in sizes_for_product(row)],
            "colors": json.loads(row["colors_json"] or "[]"),
            "has_color": row["slug"] in color_slugs or bool(json.loads(row["colors_json"] or "[]")),
            "print_areas": [{"id": key, "label": label} for key, label in PRINT_AREAS.get(row["slug"], [("front", "Front panel")])],
            "product_image": row["image"] or "",
            "photos": json.loads(row["photo_views_json"] or "{}") if "photo_views_json" in row.keys() else {},
            "photo_print_areas": json.loads(row["photo_print_areas_json"] or "{}") if "photo_print_areas_json" in row.keys() else {},
            "model_file": row["model_file"] or "" if "model_file" in row.keys() else "",
            "model_license": row["model_license"] or "" if "model_license" in row.keys() else "",
            "print_surfaces": json.loads(row["print_surfaces_json"] or "{}") if "print_surfaces_json" in row.keys() else {},
        }
        for row in rows
    }


@app.get("/api/product-assets/<slug>")
def product_assets(slug):
    product = connect_db().execute("SELECT model_file,model_license,print_surfaces_json FROM products WHERE slug=? AND enabled=1", (slug,)).fetchone()
    if product is None:
        abort(404)
    try:
        surfaces = json.loads(product["print_surfaces_json"] or "{}")
    except (ValueError, TypeError):
        surfaces = {}
    return jsonify(model=(product["model_file"] or None), license=(product["model_license"] or None), print_surfaces=surfaces)


def wants_json_response():
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def add_cart_error(message, slug, status=400):
    if wants_json_response():
        return jsonify(success=False, message=message), status
    flash(message, "error")
    return redirect(url_for("design", product=slug))


@app.get("/favicon.ico")
def favicon():
    return send_from_directory(app.static_folder, "favicon.svg", mimetype="image/svg+xml")


@app.route("/")
def home():
    rows = product_rows()
    settings=settings_map()
    featured=[row for row in rows if row["featured"]]
    return render_template("index.html", products=rows, featured_products=(featured or rows)[:4], selected=get_product("tshirt"),
                           sizes=sizes_for_product(get_product("tshirt")), printing_charge=setting_price("printing_charge",PRINTING_CHARGE),
                           hero_title=settings.get("hero_title","Your Ideas. Your Design. Your Style."),hero_subtitle=settings.get("hero_subtitle","Turn your favourite photos and creative ideas into beautiful custom printed products."),promo_text=settings.get("promo_text","Made for your moments."),settings_banner=settings.get("banner_image","images/storefront-banner-reference.png"),
                           homepage_banners=homepage_banners_from_settings(settings),
                           studio_catalog=studio_catalog(rows), max_artworks=MAX_PHOTOS_PER_PRODUCT,
                           max_create_total_mb=MAX_TOTAL_UPLOAD_BYTES // (1024 * 1024))


@app.route("/products")
def products():
    rows = product_rows()
    return render_template("products.html", products=rows, settings_banner=settings_map().get("banner_image","images/storefront-banner-reference.png"), selected=get_product("tshirt"),
                           sizes=sizes_for_product(get_product("tshirt")), printing_charge=setting_price("printing_charge",PRINTING_CHARGE),
                           studio_catalog=studio_catalog(rows), max_artworks=MAX_PHOTOS_PER_PRODUCT,
                           max_create_total_mb=MAX_TOTAL_UPLOAD_BYTES // (1024 * 1024))


@app.route("/design")
def design():
    slug = request.args.get("product", "tshirt")
    product = get_product(slug)
    if product is None:
        abort(404)
    return render_template("design.html", products=product_rows(), selected=product,
                           sizes=sizes_for_product(product), printing_charge=float(product["printing_charge"]),
                           studio_catalog=studio_catalog(), max_artworks=MAX_PHOTOS_PER_PRODUCT,
                           max_create_total_mb=MAX_TOTAL_UPLOAD_BYTES // (1024 * 1024))


@app.post("/api/design-assistant")
def design_assistant():
    if not request.is_json:
        return jsonify(success=False, message="Send the assistant request as JSON."), 415
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(success=False, message="Send a JSON object with your assistant request."), 400
    message = body.get("message", "")
    if not isinstance(message, str) or not message.strip() or len(message) > 1000:
        return jsonify(success=False, message="Enter a request of 1 to 1000 characters."), 400
    product = body.get("product")
    if product not in PRINT_AREAS:
        product = "tshirt"
    raw_count = body.get("image_count", 0)
    image_count = raw_count if isinstance(raw_count, int) and not isinstance(raw_count, bool) else 0
    requested_location = body.get("location")
    product_locations = {key for key, _label in PRINT_AREAS[product]}
    context = {
        "product": product,
        "location": requested_location if isinstance(requested_location, str) and requested_location in product_locations else PRINT_AREAS[product][0][0],
        "available_locations": [key for key, _label in PRINT_AREAS[product]],
        "available_colors": ["white", "black", "navy", "red", "blue", "pink", "yellow"] if product in {"tshirt", "mug", "pillow", "phonecase", "gift"} else [],
        "image_count": max(0, min(MAX_PHOTOS_PER_PRODUCT, image_count)),
        "image_width": body.get("image_width") if isinstance(body.get("image_width"), int) and not isinstance(body.get("image_width"), bool) else None,
        "image_height": body.get("image_height") if isinstance(body.get("image_height"), int) and not isinstance(body.get("image_height"), bool) else None,
        "has_text_selection": body.get("has_text_selection") is True,
        "conversation": [
            {"role": turn.get("role"), "content": turn.get("content", "")[:1000]}
            for turn in body.get("conversation", [])[-8:]
            if isinstance(turn, dict) and turn.get("role") in {"user", "assistant"}
            and isinstance(turn.get("content"), str)
        ] if isinstance(body.get("conversation", []), list) else [],
    }
    try:
        result = assistant_response(message.strip(), context, {slug: {key for key, _label in choices} for slug, choices in PRINT_AREAS.items()})
    except AssistantError as error:
        return jsonify(success=False, message=str(error)), 503
    return jsonify(success=True, **result)


@app.post("/cart/add")
def add_to_cart():
    slug = request.form.get("product", "")
    product = get_product(slug)
    if product is None:
        abort(400, "Choose a valid product.")
    images = [file for file in request.files.getlist("design_images") if file and file.filename]
    if not images:
        legacy_image = request.files.get("design_image")
        if legacy_image and legacy_image.filename:
            images = [legacy_image]
    design_mode = request.form.get("design_mode", "create")
    if design_mode not in {"create", "ready"}:
        return add_cart_error("Choose a valid design workflow.", slug)
    if not images:
        return add_cart_error("Upload a print-ready image before adding your design to the cart.", slug)
    if design_mode == "ready" and len(images) != 1:
        return add_cart_error("Upload one completed design in ready-made mode.", slug)
    if len(images) > MAX_PHOTOS_PER_PRODUCT:
        return add_cart_error(f"Add up to {MAX_PHOTOS_PER_PRODUCT} photos to one product.", slug)
    settings_raw = request.form.get("artwork_settings", "[]")
    try:
        settings_list = json.loads(settings_raw)
        if not isinstance(settings_list, list):
            raise ValueError("Invalid artwork settings")
        design_snapshot = json.loads(request.form.get("design_snapshot", "{}"))
        if not isinstance(design_snapshot, dict):
            raise ValueError("Invalid design snapshot")
        requested_view = design_snapshot.get("view", "front")
        if requested_view not in {"front", "back", "left", "right", "side", "detail"}:
            raise ValueError("Choose a supported product view.")
        normalized = []
        total_size = 0
        for index, image in enumerate(images):
            ext = Path(secure_filename(image.filename)).suffix.lower().lstrip(".")
            allowed_extensions = ALLOWED_EXTENSIONS | ({"webp"} if design_mode == "ready" else set())
            if ext not in allowed_extensions:
                formats = "PNG, JPG, JPEG or WebP" if design_mode == "ready" else "PNG, JPG or JPEG"
                raise ValueError(f"Use {formats} files only.")
            image.stream.seek(0, os.SEEK_END)
            size = image.stream.tell()
            image.stream.seek(0)
            total_size += size
            if design_mode == "create" and total_size > MAX_TOTAL_UPLOAD_BYTES:
                raise ValueError(f"Combined artwork files must total {MAX_TOTAL_UPLOAD_BYTES // (1024 * 1024)} MB or less.")
            upload_limit = MAX_READY_UPLOAD_BYTES if design_mode == "ready" else MAX_UPLOAD_BYTES
            if size > upload_limit:
                raise ValueError(f"Each image must be {upload_limit // (1024 * 1024)} MB or smaller.")
            with Image.open(image.stream) as im:
                expected_formats = {"png": "PNG", "jpg": "JPEG", "jpeg": "JPEG", "webp": "WEBP"}
                if im.format != expected_formats[ext]:
                    raise ValueError(f"The selected file does not contain a valid {ext.upper()} image.")
                im.verify()
            image.stream.seek(0)
            with Image.open(image.stream) as im:
                width, height = im.size
                min_dimension = 1 if design_mode == "ready" else 80
                if width < min_dimension or height < min_dimension or width * height > MAX_IMAGE_PIXELS:
                    raise ValueError(f"Image dimensions must be positive and no more than {MAX_IMAGE_PIXELS // 1_000_000} megapixels.")
            raw = settings_list[index] if index < len(settings_list) and isinstance(settings_list[index], dict) else {}
            available_areas = PRINT_AREAS.get(slug, [("front", "Front panel")])
            locations = {key for key, _label in available_areas}
            location = raw.get("location", available_areas[0][0])
            if location not in locations:
                raise ValueError(f"{location} is not a supported print location for this product.")
            text_spec = raw.get("text_spec")
            if text_spec is not None:
                if not isinstance(text_spec, dict):
                    raise ValueError("Text layer settings are invalid.")
                text = text_spec.get("text")
                font = text_spec.get("font")
                color = text_spec.get("color")
                font_size = text_spec.get("size")
                if not isinstance(text, str) or not text.strip() or len(text) > 80:
                    raise ValueError("Text layers must contain 1 to 80 characters.")
                if font not in {"Arial", "Georgia", "Impact", "Trebuchet MS"}:
                    raise ValueError("Choose a supported text font.")
                if color not in {"black", "white", "red", "blue", "navy", "purple", "green"}:
                    raise ValueError("Choose a supported text colour.")
                if isinstance(font_size, bool) or not isinstance(font_size, (int, float)) or not 30 <= float(font_size) <= 180:
                    raise ValueError("Text size must be between 30 and 180.")
                if not isinstance(text_spec.get("bold"), bool) or not isinstance(text_spec.get("italic"), bool):
                    raise ValueError("Text style settings are invalid.")
                text_spec = {"text": text.strip(), "font": font, "color": color, "size": int(font_size),
                             "bold": text_spec["bold"], "italic": text_spec["italic"],
                             "alignment": "center"}
            normalized.append({
                "file": image, "ext": ext,
                "size": size, "width": width, "height": height,
                "location": location,
                "scale": max(.3, min(3.0, finite_number(raw.get("scale", 1), 1))),
                "rotation": max(-180, min(180, finite_number(raw.get("rotation", 0)))),
                "offset_x": max(-10000, min(10000, finite_number(raw.get("offset_x", 0)))),
                "offset_y": max(-10000, min(10000, finite_number(raw.get("offset_y", 0)))),
                "position_x": max(-.5, min(.5, finite_number(raw.get("position_x", 0)))),
                "position_y": max(-.5, min(.5, finite_number(raw.get("position_y", 0)))),
                "view": raw.get("view") if raw.get("view") in {"front", "back", "left", "right", "side", "detail"} else requested_view,
                "layer_order": index,
                "layer_id": raw.get("layer_id") if isinstance(raw.get("layer_id"), str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", raw.get("layer_id")) else secrets.token_hex(8),
                "layer_type": "text" if text_spec else ("ready-artwork" if design_mode == "ready" else "image"),
                "text_spec": text_spec,
            })
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        formats = "PNG, JPG, JPEG or WebP" if design_mode == "ready" else "PNG, JPG or JPEG"
        limit_mb = MAX_READY_UPLOAD_BYTES // (1024 * 1024) if design_mode == "ready" else MAX_UPLOAD_BYTES // (1024 * 1024)
        if isinstance(error, Image.DecompressionBombError):
            message = f"Image resolution exceeds the {MAX_IMAGE_PIXELS // 1_000_000} megapixel limit."
        else:
            message = str(error) if isinstance(error, ValueError) and str(error) else f"One or more images are invalid. Use {formats} files up to {limit_mb} MB each."
        return add_cart_error(message, slug)

    saved_paths = []
    artworks = []
    try:
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        for entry in normalized:
            filename = f"{secrets.token_hex(12)}.{entry['ext']}"
            target = UPLOAD_DIR / filename
            saved_paths.append(target)
            entry["file"].stream.seek(0)
            entry["file"].save(target)
            if not target.is_file() or target.stat().st_size != entry["size"]:
                raise OSError("The uploaded image was not saved completely.")
            artworks.append({key: entry[key] for key in ("scale", "rotation", "offset_x", "offset_y", "position_x", "position_y", "view", "layer_order", "layer_id", "layer_type", "location", "text_spec")} | {
                "artwork_id": secrets.token_hex(12), "image": filename,
                "original_name": secure_filename(entry["file"].filename)[:120] or f"artwork-{len(artworks) + 1}.{entry['ext']}",
                "size": entry["size"], "width": entry["width"], "height": entry["height"],
                "content_type": entry["file"].mimetype or f"image/{'jpeg' if entry['ext'] in {'jpg', 'jpeg'} else entry['ext']}",
            })
    except OSError:
        for path in saved_paths:
            path.unlink(missing_ok=True)
        app.logger.exception("Image upload could not be saved completely")
        return add_cart_error("The image could not be saved. Please try uploading it again.", slug, status=500)
    sizes = dict(sizes_for_product(product))
    selected_size = request.form.get("size", next(iter(sizes)))
    if selected_size not in sizes:
        for artwork in artworks:
            (UPLOAD_DIR / artwork["image"]).unlink(missing_ok=True)
        abort(400, "Choose a valid size.")
    try:
        quantity = max(1, min(99, int(request.form.get("quantity", "1"))))
        scale = max(0.3, min(3.0, float(request.form.get("scale", "1"))))
        rotation = max(-180, min(180, float(request.form.get("rotation", "0"))))
        offset_x = max(-100, min(100, float(request.form.get("offset_x", "0"))))
        offset_y = max(-100, min(100, float(request.form.get("offset_y", "0"))))
    except ValueError:
        for artwork in artworks:
            (UPLOAD_DIR / artwork["image"]).unlink(missing_ok=True)
        abort(400, "Invalid design settings.")
    if quantity > int(product["inventory"]):
        for artwork in artworks:
            (UPLOAD_DIR / artwork["image"]).unlink(missing_ok=True)
        return add_cart_error("The selected product does not have enough stock for that quantity.",slug)
    color = request.form.get("color", "white")
    allowed_product_colors=json.loads(product["colors_json"] or "[]")
    if not allowed_product_colors or color not in allowed_product_colors:
        color = allowed_product_colors[0] if allowed_product_colors else "white"
    discount_percent=max(0,min(100,float(product["discount_percent"] or 0)))
    printing=float(product["printing_charge"]);design_charge=float(product["design_charge"])
    undiscounted=float(product["price"])+sizes[selected_size]+printing+design_charge
    discount=round(undiscounted*discount_percent/100,2)
    unit=round(undiscounted-discount,2)
    item_id = secrets.token_hex(8)
    view_angle = finite_number(design_snapshot.get("product_angle", 0))
    item_design = {
        "schema_version": 1, "design_id": secrets.token_hex(12), "product": slug,
        "variant": selected_size, "color": color, "mode": design_mode,
        "view": requested_view, "product_angle": max(-3600, min(3600, view_angle)),
        "product_zoom": max(.7, min(1.3, finite_number(design_snapshot.get("product_zoom", 1), 1))),
        "default_location": design_snapshot.get("default_location") if design_snapshot.get("default_location") in {key for key, _ in PRINT_AREAS.get(slug, [])} else artworks[0]["location"],
        "print_areas": sorted({artwork["location"] for artwork in artworks}),
        "layers": [{key: artwork[key] for key in ("artwork_id", "layer_id", "layer_order", "layer_type", "image", "original_name", "width", "height", "size", "content_type", "location", "view", "scale", "rotation", "position_x", "position_y", "text_spec")} for artwork in artworks],
    }
    item = {"id": item_id, "design_id": item_design["design_id"], "design": item_design,
            "slug": slug, "name": product["name"], "image": artworks[0]["image"], "artworks": artworks,
            "design_mode": design_mode, "size": selected_size, "color": color, "quantity": quantity, "scale": scale,
            "rotation": rotation, "offset_x": offset_x, "offset_y": offset_y,
            "base_price": float(product["price"]), "size_price": sizes[selected_size],
            "printing_charge": printing,"design_charge":design_charge,"discount":discount,"discount_percent":discount_percent,"unit_price":unit}
    items = cart_items()
    items.append(item)
    session["cart"] = items
    if wants_json_response():
        return jsonify(
            success=True, message="Your design has been added to the cart.",
            cart_count=sum(int(cart_item["quantity"]) for cart_item in items),
            cart_total=cart_total(items), cart_url=url_for("cart"), checkout_url=url_for("checkout"),
            items=[{
                "name": cart_item["name"], "size": cart_item["size"],
                "color": cart_item["color"], "quantity": cart_item["quantity"],
                "line_total": float(cart_item["unit_price"]) * int(cart_item["quantity"]),
                "artwork_count": len(cart_item.get("artworks", [])),
                "design_mode": cart_item.get("design_mode", "create"),
                "artworks": [{
                    "filename": artwork["image"],
                    "image_url": url_for("uploaded_file", filename=artwork["image"]),
                    "content_type": artwork.get("content_type", "application/octet-stream"),
                    "size": artwork.get("size", (UPLOAD_DIR / artwork["image"]).stat().st_size if (UPLOAD_DIR / artwork["image"]).is_file() else 0),
                    "width": artwork.get("width"), "height": artwork.get("height"),
                    "location": artwork.get("location"), "scale": artwork.get("scale"),
                    "rotation": artwork.get("rotation"), "offset_x": artwork.get("offset_x"),
                    "offset_y": artwork.get("offset_y"),
                } for artwork in cart_item.get("artworks", [])],
            } for cart_item in items],
        ), 201
    flash("Your design has been added to the cart.", "success")
    return redirect(url_for("cart"))


@app.route("/cart")
def cart():
    items = current_cart_items(); session["cart"] = items
    return render_template("cart.html", items=items, total=cart_total(items), delivery_charge=setting_price("delivery_charge"))


@app.post("/cart/remove/<item_id>")
def remove_from_cart(item_id):
    items = cart_items()
    removed = [item for item in items if item["id"] == item_id]
    session["cart"] = [item for item in items if item["id"] != item_id]
    for item in removed:
        for artwork in item.get("artworks", [{"image": item.get("image")} ]):
            if artwork.get("image"):
                (UPLOAD_DIR / artwork["image"]).unlink(missing_ok=True)
    return redirect(url_for("cart"))


@app.route("/checkout", methods=["GET", "POST"])
def checkout():
    items = current_cart_items(); session["cart"] = items
    if not items:
        return redirect(url_for("cart"))
    delivery = setting_price("delivery_charge")
    customer = connect_db().execute("SELECT * FROM customers WHERE id=?", (session.get("customer_id"),)).fetchone() if session.get("role") == "customer" else None
    if request.method == "POST":
        fields = {key: request.form.get(key, "").strip() for key in ("customer_name", "email", "phone", "address")}
        if customer:
            fields.update(customer_name=customer["name"], email=customer["email"], phone=customer["phone"])
        if not all(fields.values()) or "@" not in fields["email"]:
            flash("Please complete all details with a valid email address.", "error")
            return render_template("checkout.html", items=items, total=cart_total(items)+delivery, delivery_charge=delivery, customer=customer)
        db = connect_db(); order_total = cart_total(items) + delivery
        try:
            db.execute("BEGIN IMMEDIATE")
            quantities={}
            for item in items:quantities[item["slug"]]=quantities.get(item["slug"],0)+int(item["quantity"])
            for slug,quantity in quantities.items():
                changed=db.execute("UPDATE products SET inventory=inventory-? WHERE slug=? AND inventory>=?",(quantity,slug,quantity))
                if changed.rowcount!=1:raise ValueError("Stock changed while you were checking out. Review your cart and try again.")
            cursor = db.execute("INSERT INTO orders (customer_name,email,phone,address,items_json,total,created_at,customer_id,status,delivery_charge) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (fields["customer_name"], fields["email"], fields["phone"], fields["address"], json.dumps(items), order_total,
                 datetime.now().isoformat(timespec="seconds"), customer["id"] if customer else None, "Pending", delivery))
            order_id = cursor.lastrowid
            order_snapshot = json.loads(json.dumps(items))
            for order_item in order_snapshot:
                order_item["order_id"] = order_id
                order_item["customer_id"] = customer["id"] if customer else None
                design = order_item.get("design")
                if isinstance(design, dict):
                    design.update(order_id=order_id, customer_id=customer["id"] if customer else None)
                for artwork in order_item.get("artworks", []):
                    artwork["order_id"] = order_id
                    artwork["customer_id"] = customer["id"] if customer else None
            db.execute("UPDATE orders SET items_json=? WHERE id=?", (json.dumps(order_snapshot), order_id))
            db.commit()
        except ValueError as error:
            db.rollback();flash(str(error),"error")
            return render_template("checkout.html",items=items,total=order_total,delivery_charge=delivery,customer=customer),409
        session["cart"] = []
        return render_template("checkout.html", items=[], total=0, order_id=order_id, delivery_charge=0, customer=customer)
    return render_template("checkout.html", items=items, total=cart_total(items)+delivery, delivery_charge=delivery, customer=customer)


@app.errorhandler(413)
def file_too_large(_error):
    message = f"Request exceeds the server upload cap. Ready-Made files are limited to {MAX_READY_UPLOAD_BYTES // (1024 * 1024)} MB; Create Your Own accepts up to {MAX_PHOTOS_PER_PRODUCT} files at {MAX_UPLOAD_BYTES // (1024 * 1024)} MB each and {MAX_TOTAL_UPLOAD_BYTES // (1024 * 1024)} MB total."
    if wants_json_response():
        return jsonify(success=False, message=message), 413
    flash(message, "error")
    return redirect(url_for("design"))


def customer_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("role") != "customer" or not session.get("customer_id"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("role") != "admin" or not session.get("admin_id"):
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def safe_next_url(value):
    if isinstance(value, str) and value.startswith("/") and not value.startswith("//"):
        return value
    return url_for("home")


def parse_price(value, label, maximum=1_000_000):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"Enter a valid {label}.")
    if not 0 <= number <= maximum:
        raise ValueError(f"{label.capitalize()} must be between 0 and {maximum}.")
    return round(number, 2)


def customer_by_id(customer_id):
    return connect_db().execute("SELECT id,name,email,phone,created_at FROM customers WHERE id=?", (customer_id,)).fetchone()


def customer_notifications(customer_id):
    return connect_db().execute(
        "SELECT id,order_id,message,created_at FROM customer_notifications WHERE customer_id=? ORDER BY id DESC",
        (customer_id,),
    ).fetchall()


def order_progress_index(status):
    if status == "Delivered":
        return 3
    if status == "Shipped":
        return 2
    if status in {"Processing", "Confirmed"}:
        return 1
    return 0


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_template("register.html")
    name = request.form.get("name", "").strip()[:120]
    email = request.form.get("email", "").strip().lower()[:254]
    phone = request.form.get("phone", "").strip()[:32]
    password = request.form.get("password", "")
    if not name or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or not phone or len(password) < 10:
        flash("Enter your name, a valid email, phone number, and a password with at least 10 characters.", "error")
        return render_template("register.html"), 400
    db = connect_db()
    try:
        db.execute("INSERT INTO customers(name,email,phone,password_hash,created_at) VALUES(?,?,?,?,?)",
                   (name, email, phone, generate_password_hash(password), datetime.now().isoformat(timespec="seconds")))
        db.commit()
    except sqlite3.IntegrityError:
        flash("An account already exists for that email address.", "error")
        return render_template("register.html"), 409
    flash("Your account is ready. Please sign in.", "success")
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    customer = connect_db().execute("SELECT * FROM customers WHERE email=?", (email,)).fetchone()
    if customer is None or not check_password_hash(customer["password_hash"], password):
        flash("Email or password was not recognized.", "error")
        return render_template("login.html"), 401
    cart = session.get("cart", [])
    session.clear()
    session["cart"] = cart
    session["role"] = "customer"
    session["customer_id"] = customer["id"]
    session.permanent = True
    return redirect(safe_next_url(request.form.get("next") or request.args.get("next")))


@app.post("/logout")
def logout():
    cart = session.get("cart", [])
    session.clear()
    if cart:
        session["cart"] = cart
    flash("You have been signed out.", "success")
    return redirect(url_for("home"))


@app.route("/account/profile", methods=["GET", "POST"])
@customer_required
def customer_profile():
    db = connect_db()
    customer = db.execute("SELECT * FROM customers WHERE id=?", (session["customer_id"],)).fetchone()
    if customer is None:
        session.clear()
        return redirect(url_for("login"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()[:120]
        email = request.form.get("email", "").strip().lower()[:254]
        phone = request.form.get("phone", "").strip()[:32]
        new_password = request.form.get("new_password", "")
        if not name or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or not phone:
            flash("Enter a name, valid email and phone number.", "error")
        elif new_password and len(new_password) < 10:
            flash("A new password must contain at least 10 characters.", "error")
        else:
            try:
                if new_password:
                    db.execute("UPDATE customers SET name=?,email=?,phone=?,password_hash=? WHERE id=?",
                               (name,email,phone,generate_password_hash(new_password),customer["id"]))
                else:
                    db.execute("UPDATE customers SET name=?,email=?,phone=? WHERE id=?", (name,email,phone,customer["id"]))
                db.commit(); flash("Profile updated.", "success")
                customer = db.execute("SELECT * FROM customers WHERE id=?", (session["customer_id"],)).fetchone()
            except sqlite3.IntegrityError:
                flash("That email address is already in use.", "error")
    return render_template("profile.html", customer=customer, notifications=customer_notifications(customer["id"]))


@app.get("/account/orders")
@customer_required
def customer_orders():
    orders = connect_db().execute("SELECT id,total,status,created_at,delivered_at FROM orders WHERE customer_id=? ORDER BY id DESC", (session["customer_id"],)).fetchall()
    notifications = customer_notifications(session["customer_id"])
    return render_template("account_orders.html", orders=orders, notifications=notifications)


@app.get("/account/orders/<int:order_id>")
@customer_required
def customer_order_detail(order_id):
    order = connect_db().execute("SELECT * FROM orders WHERE id=? AND customer_id=?", (order_id,session["customer_id"])).fetchone()
    if order is None:
        abort(404)
    return render_template("order_detail.html", order=order, items=json.loads(order["items_json"]),
                           progress_steps=["Order Placed", "Processing", "Shipped", "Delivered"],
                           progress_index=order_progress_index(order["status"]))


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "GET":
        return render_template("admin_login.html")
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    admin = connect_db().execute("SELECT * FROM admin_users WHERE email=?", (email,)).fetchone()
    if admin is None or not check_password_hash(admin["password_hash"], password):
        flash("Admin email or password was not recognized.", "error")
        return render_template("admin_login.html"), 401
    session.clear(); session["role"]="admin"; session["admin_id"]=admin["id"]; session.permanent=True
    next_url=request.form.get("next") or request.args.get("next")
    return redirect(safe_next_url(next_url) if next_url else "/admin")


def mail_configuration():
    server = os.environ.get("MAIL_SERVER", "").strip()
    sender = os.environ.get("MAIL_DEFAULT_SENDER", "").strip()
    username = os.environ.get("MAIL_USERNAME", "").strip()
    password = os.environ.get("MAIL_PASSWORD", "")
    try:
        port = int(os.environ.get("MAIL_PORT", ""))
    except ValueError:
        return None
    use_tls = os.environ.get("MAIL_USE_TLS", "").strip().lower() in {"1", "true", "yes", "on"}
    if not server or not sender or port < 1 or port > 65535 or not username or not password:
        return None
    return server, port, username, password, sender, use_tls


def recovery_rate_limited(bucket, identity, limit, window_seconds):
    """Atomically consume a database-backed fixed-window recovery attempt."""
    now = int(time.time())
    key_hash = hmac.new(str(app.secret_key).encode(), identity.encode(), hashlib.sha256).hexdigest()
    db = connect_db()
    db.execute("BEGIN IMMEDIATE")
    row = db.execute("SELECT window_started,attempts FROM admin_recovery_limits WHERE bucket=? AND key_hash=?",
                     (bucket, key_hash)).fetchone()
    if row is None or now - row[0] >= window_seconds:
        db.execute("INSERT INTO admin_recovery_limits(bucket,key_hash,window_started,attempts) VALUES(?,?,?,1) "
                   "ON CONFLICT(bucket,key_hash) DO UPDATE SET window_started=excluded.window_started,attempts=1",
                   (bucket, key_hash, now))
        limited = False
    else:
        limited = row[1] >= limit
        if not limited:
            db.execute("UPDATE admin_recovery_limits SET attempts=attempts+1 WHERE bucket=? AND key_hash=?",
                       (bucket, key_hash))
    db.execute("DELETE FROM admin_recovery_limits WHERE window_started<?", (now - 86400,))
    db.commit()
    return limited


def recovery_headers(response):
    response = app.make_response(response)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.route("/admin/forgot-password", methods=["GET", "POST"])
def admin_forgot_password():
    if request.method == "GET":
        return recovery_headers(render_template("admin_forgot_password.html"))
    email = request.form.get("email", "").strip().lower()[:254]
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        flash("Enter a valid email address.", "error")
        return recovery_headers(render_template("admin_forgot_password.html")), 400
    ip = request.remote_addr or "unknown"
    email_identity = hashlib.sha256(email.encode()).hexdigest()
    if recovery_rate_limited("request-ip", ip, 5, 900) or recovery_rate_limited("request-email", email_identity, 3, 900):
        return recovery_headers(("If an admin account exists for that email, password reset instructions have been sent.", 429))
    mail = mail_configuration()
    if mail is None:
        return recovery_headers(("Password reset is temporarily unavailable. Please try again later.", 503))
    admin = connect_db().execute("SELECT id,email FROM admin_users WHERE email=?", (email,)).fetchone()
    if admin is not None:
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        db = connect_db()
        db.execute("DELETE FROM admin_password_resets WHERE admin_id=? OR expires_at<=?", (admin["id"], int(time.time())))
        db.execute("INSERT INTO admin_password_resets(token_hash,admin_id,expires_at) VALUES(?,?,?)",
                   (token_hash, admin["id"], int(time.time()) + 900))
        db.commit()
        # URL fragments never reach the HTTP server or access logs.
        reset_url = url_for("admin_reset_password", _external=True) + "#" + raw_token
        message = EmailMessage()
        message["Subject"] = "ANSUMAN GRAPHIC admin password reset"
        message["From"] = mail[4]
        message["To"] = admin["email"]
        message.set_content(f"A password reset was requested for your admin account. Use this link within 15 minutes:\n\n{reset_url}\n\nIf you did not request this, you can ignore this email.")
        try:
            with smtplib.SMTP(mail[0], mail[1], timeout=15) as smtp:
                smtp.ehlo()
                if mail[5]:
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.ehlo()
                smtp.login(mail[2], mail[3])
                smtp.send_message(message)
        except (OSError, smtplib.SMTPException):
            db.execute("DELETE FROM admin_password_resets WHERE token_hash=?", (token_hash,))
            db.commit()
            app.logger.warning("Admin password reset email delivery failed")
            flash("We could not send the reset email. Please try again later.", "error")
            return recovery_headers(("Password reset is temporarily unavailable. Please try again later.", 503))
    flash("If an admin account exists for that email, password reset instructions have been sent.", "success")
    return recovery_headers(redirect(url_for("admin_forgot_password")))


@app.route("/admin/reset-password", methods=["GET", "POST"])
def admin_reset_password():
    db = connect_db()
    if request.method == "GET":
        token_hash = session.get("admin_reset_token_hash", "")
        reset = db.execute("SELECT admin_id FROM admin_password_resets WHERE token_hash=? AND used_at IS NULL AND expires_at>?",
                           (token_hash, int(time.time()))).fetchone() if token_hash else None
        return recovery_headers((render_template("admin_reset_password.html", invalid=reset is None), 200 if reset else 400))
    token = request.form.get("token", "")[:200]
    if token:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        limited = recovery_rate_limited("token-ip", request.remote_addr or "unknown", 10, 3600)
        limited = recovery_rate_limited("token", token_hash, 5, 3600) or limited
        if limited:
            return recovery_headers(("Too many recovery attempts. Request a new reset link later.", 429))
        reset = db.execute(
            "SELECT admin_id FROM admin_password_resets WHERE token_hash=? AND used_at IS NULL AND expires_at>?",
            (token_hash, int(time.time()))).fetchone()
        if reset is None:
            return recovery_headers(("This reset link is invalid, expired, or already used.", 400))
        session["admin_reset_token_hash"] = token_hash
        return recovery_headers(redirect(url_for("admin_reset_password")))
    token_hash = session.get("admin_reset_token_hash", "")
    if recovery_rate_limited("submit-ip", request.remote_addr or "unknown", 10, 3600):
        return recovery_headers(("Password reset could not be completed. Request a new reset link.", 429))
    password = request.form.get("password", "")
    confirmation = request.form.get("confirm_password", "")
    if password != confirmation or len(password) < 12 or not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
        flash("Passwords must match and contain at least 12 characters, including a letter and a number.", "error")
        return recovery_headers((render_template("admin_reset_password.html", invalid=False), 400))
    db.execute("BEGIN IMMEDIATE")
    reset = db.execute("SELECT admin_id FROM admin_password_resets WHERE token_hash=? AND used_at IS NULL AND expires_at>?",
                       (token_hash, int(time.time()))).fetchone()
    if reset is None:
        db.rollback()
        session.pop("admin_reset_token_hash", None)
        return recovery_headers((render_template("admin_reset_password.html", invalid=True), 400))
    db.execute("UPDATE admin_users SET password_hash=? WHERE id=?", (generate_password_hash(password), reset["admin_id"]))
    db.execute("UPDATE admin_password_resets SET used_at=? WHERE token_hash=?", (int(time.time()), token_hash))
    db.commit()
    session.pop("admin_reset_token_hash", None)
    flash("Your password has been reset. Sign in with your new password.", "success")
    return recovery_headers(redirect(url_for("admin_login")))


@app.cli.command("create-admin")
def create_admin_command():
    """Create a separate administrator account using interactive password prompts."""
    import getpass
    email = input("Admin email: ").strip().lower()
    password = getpass.getpass("Admin password (10+ characters): ")
    confirmation = getpass.getpass("Confirm password: ")
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or len(password) < 10 or password != confirmation:
        raise SystemExit("Invalid email or password confirmation; no admin was created.")
    db = sqlite3.connect(DATABASE)
    try:
        db.execute("INSERT INTO admin_users(email,password_hash,created_at) VALUES(?,?,?)",
                   (email,generate_password_hash(password),datetime.now().isoformat(timespec="seconds")))
        db.commit()
    except sqlite3.IntegrityError:
        raise SystemExit("An admin with that email already exists.")
    finally:
        db.close()
    print("Admin account created.")


@app.cli.command("reset-admin-password")
def reset_admin_password_command():
    """Securely reset the password for one existing administrator account."""
    import getpass

    email = input("Existing admin email: ").strip().lower()
    password = getpass.getpass("New admin password (12+ characters, include a letter and number): ")
    confirmation = getpass.getpass("Confirm new admin password: ")
    if password != confirmation:
        raise SystemExit("Password confirmation does not match; no password was changed.")
    if len(password) < 12 or not any(char.isalpha() for char in password) or not any(char.isdigit() for char in password):
        raise SystemExit("Password must be at least 12 characters and include at least one letter and one number; no password was changed.")
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise SystemExit("Enter a valid existing admin email; no password was changed.")

    db = sqlite3.connect(DATABASE)
    try:
        db.execute("BEGIN IMMEDIATE")
        admin = db.execute("SELECT id FROM admin_users WHERE email=?", (email,)).fetchone()
        if admin is None:
            db.rollback()
            raise SystemExit("No admin account exists for that email; no password was changed.")
        db.execute("UPDATE admin_users SET password_hash=? WHERE id=?",
                   (generate_password_hash(password), admin[0]))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    print("Admin password updated successfully.")


@app.post("/admin/logout")
@admin_required
def admin_logout():
    session.clear(); flash("Admin signed out.", "success")
    return redirect(url_for("admin_login"))


ADMIN_SECTIONS = [
    ("overview", "OV", "Dashboard Overview"), ("products", "PR", "Product Management"),
    ("categories", "CA", "Category Management"), ("prices", "PR$", "Price Management"),
    ("inventory", "ST", "Inventory Management"), ("orders", "OR", "Order Management"),
    ("completed", "OK", "Completed Orders"), ("customers", "CU", "Customer Management"), ("website", "WB", "Website Banners"),
    ("offers", "OF", "Offers and Discounts"), ("reports", "SR", "Sales Reports"),
    ("settings", "SE", "Website Settings"),
]


def admin_product_rows(db=None):
    """Return product rows with values formatted for the admin edit form."""
    db = db or connect_db()
    rows = db.execute("SELECT p.*,c.name category_name FROM products p LEFT JOIN categories c ON p.category_id=c.id ORDER BY p.rowid DESC").fetchall()
    products = []
    for row in rows:
        product = dict(row)
        product["sizes_text"] = "\n".join(
            f"{label}={extra:g}" for label, extra in sizes_for_product(row)
        )
        try:
            colors = json.loads(row["colors_json"] or "[]")
            if not isinstance(colors, list):
                colors = []
        except (ValueError, TypeError):
            colors = []
        product["colors_text"] = ", ".join(str(color) for color in colors if isinstance(color, str))
        try:
            photo_views = json.loads(row["photo_views_json"] or "{}")
            product["photo_views"] = photo_views if isinstance(photo_views, dict) else {}
        except (ValueError, TypeError):
            product["photo_views"] = {}
        try:
            photo_print_areas = json.loads(row["photo_print_areas_json"] or "{}")
            product["photo_print_areas_json"] = json.dumps(photo_print_areas if isinstance(photo_print_areas, dict) else {}, separators=(",", ":"))
        except (ValueError, TypeError):
            product["photo_print_areas_json"] = "{}"
        products.append(product)
    return products


def admin_render(section, **context):
    labels={entry[0]:entry[2] for entry in ADMIN_SECTIONS}
    return render_template("admin.html", section=section, section_title=labels.get(section,"Admin"), sections=ADMIN_SECTIONS, **context)


def save_product_image(file):
    if not file or not file.filename:
        return None
    safe_name = secure_filename(file.filename)
    ext = Path(safe_name).suffix.lower().lstrip(".")
    if ext not in {"png", "jpg", "jpeg", "webp"}:
        raise ValueError("Product images must be PNG, JPG or WebP.")
    file.stream.seek(0, os.SEEK_END); size=file.stream.tell(); file.stream.seek(0)
    if size > 8 * 1024 * 1024:
        raise ValueError("Product images must be 8 MB or smaller.")
    try:
        with Image.open(file.stream) as image:
            expected={"png":"PNG","jpg":"JPEG","jpeg":"JPEG","webp":"WEBP"}[ext]
            if image.format != expected:
                raise ValueError("The product image extension does not match its actual format.")
            if image.width * image.height > 40_000_000:
                raise ValueError("Product image resolution must not exceed 40 megapixels.")
            image.verify()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as error:
        raise ValueError("Choose a valid image file.") from error
    file.stream.seek(0)
    target=BASE_DIR / "static" / "uploads" / "products";target.mkdir(parents=True,exist_ok=True)
    filename=f"{secrets.token_hex(12)}.{ext}";file.save(target/filename)
    return f"uploads/products/{filename}"


PHOTO_VIEW_FIELDS = {"front": "photos_front", "back": "photos_back", "left": "photos_left", "right": "photos_right", "detail": "photos_detail"}
VALID_PRINT_SURFACE_KEYS = {key for areas in PRINT_AREAS.values() for key, _label in areas}


def save_product_photo_views(files, current=None, replace_views=()):
    """Append validated admin photos by view; view replacement is explicit."""
    views = {key: list(value) for key, value in (current or {}).items() if key in PHOTO_VIEW_FIELDS and isinstance(value, list)}
    for view in replace_views:
        if view in PHOTO_VIEW_FIELDS:
            views[view] = []
    for view, field in PHOTO_VIEW_FIELDS.items():
        for image in files.getlist(field):
            if not image or not image.filename:
                continue
            saved = save_product_image(image)
            if saved:
                views.setdefault(view, []).append(saved)
                if len(views[view]) > 12:
                    raise ValueError(f"Keep no more than 12 {view} product photos.")
    return {view: paths for view, paths in views.items() if paths}


def parse_photo_print_areas(raw, slug):
    """Validate normalized quadrilaterals used to composite designs on product photos."""
    if not isinstance(raw, str) or len(raw) > 12000:
        raise ValueError("Photo print area settings are too large.")
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError as error:
        raise ValueError("Photo print areas must be valid JSON.") from error
    if not isinstance(value, dict):
        raise ValueError("Photo print areas must be a JSON object.")
    allowed_views = set(PHOTO_VIEW_FIELDS)
    allowed_locations = {key for key, _label in PRINT_AREAS.get(slug, [("front", "Front panel")])}
    clean = {}
    for view, areas in value.items():
        if view not in allowed_views or not isinstance(areas, dict):
            raise ValueError("Use front, back, left, right or detail as photo view keys.")
        clean[view] = {}
        for location, points in areas.items():
            if location not in allowed_locations or not isinstance(points, list) or len(points) != 4:
                raise ValueError(f"Photo print boundary for {view} must use a supported print location and four corners.")
            polygon = []
            for point in points:
                if not isinstance(point, list) or len(point) != 2:
                    raise ValueError("Each photo print boundary corner must be an [x, y] pair.")
                x, y = point
                if isinstance(x, bool) or isinstance(y, bool) or not isinstance(x, (int, float)) or not isinstance(y, (int, float)) or not (0 <= x <= 1 and 0 <= y <= 1):
                    raise ValueError("Photo print boundary coordinates must be between 0 and 1.")
                polygon.append([round(float(x), 6), round(float(y), 6)])
            crosses = []
            for index in range(4):
                x1, y1 = polygon[index]; x2, y2 = polygon[(index + 1) % 4]; x3, y3 = polygon[(index + 2) % 4]
                crosses.append((x2 - x1) * (y3 - y2) - (y2 - y1) * (x3 - x2))
            if any(abs(cross) < 1e-6 for cross in crosses) or not (all(cross > 0 for cross in crosses) or all(cross < 0 for cross in crosses)):
                raise ValueError("Photo print boundary corners must form a convex quadrilateral in clockwise or counter-clockwise order.")
            clean[view][location] = polygon
    return clean


def delete_unused_product_assets(db, photo_paths=(), model_files=()):
    """Remove only unreferenced generated assets inside their managed directories."""
    rows = db.execute("SELECT image,photo_views_json,model_file FROM products").fetchall()
    used_photos = set(); used_models = set()
    for row in rows:
        if row["image"]: used_photos.add(row["image"])
        try:
            views = json.loads(row["photo_views_json"] or "{}")
            if isinstance(views, dict):
                used_photos.update(path for group in views.values() if isinstance(group, list) for path in group if isinstance(path, str))
        except (ValueError, TypeError):
            pass
        if row["model_file"]: used_models.add(row["model_file"])
    photo_root = (BASE_DIR / "static" / "uploads" / "products").resolve()
    model_root = (BASE_DIR / "static" / "models").resolve()
    for relative in set(photo_paths) - used_photos:
        if not isinstance(relative, str) or not relative.startswith("uploads/products/"): continue
        target = (BASE_DIR / "static" / relative).resolve()
        if target.parent == photo_root:
            target.unlink(missing_ok=True)
    for filename in set(model_files) - used_models:
        if not isinstance(filename, str) or Path(filename).name != filename: continue
        target = (model_root / filename).resolve()
        if target.parent == model_root:
            target.unlink(missing_ok=True)


def save_licensed_model(file, slug, license_text):
    if not file or not file.filename:
        return None
    if not license_text or len(license_text.strip()) < 12:
        raise ValueError("Enter the model's license and attribution source before uploading it.")
    if Path(secure_filename(file.filename)).suffix.lower() != ".glb":
        raise ValueError("Upload a self-contained GLB model. GLTF files with external assets are not supported.")
    file.stream.seek(0, os.SEEK_END); size = file.stream.tell(); file.stream.seek(0)
    if size > 80 * 1024 * 1024:
        raise ValueError("A 3D model must be 80 MB or smaller.")
    header = file.stream.read(12); file.stream.seek(0)
    if len(header) != 12 or header[:4] != b"glTF" or int.from_bytes(header[4:8], "little") != 2 or int.from_bytes(header[8:12], "little") != size:
        raise ValueError("Choose a valid glTF 2.0 binary (.glb) model.")
    target = BASE_DIR / "static" / "models"; target.mkdir(parents=True, exist_ok=True)
    filename = f"{slug}-{secrets.token_hex(10)}.glb"; file.save(target / filename)
    return f"{filename}"


def parse_print_surfaces(raw, slug):
    try:
        value = json.loads(raw or "{}")
    except (ValueError, TypeError) as error:
        raise ValueError("Printable model surfaces must be valid JSON.") from error
    if not isinstance(value, dict) or any(key not in VALID_PRINT_SURFACE_KEYS or not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_. -]{1,100}", name) for key, name in value.items()):
        raise ValueError("Map supported print area IDs to safe GLB mesh names in the surface JSON.")
    return value


@app.get("/admin/section/<section>")
@admin_required
def admin_section(section):
    endpoints={"overview":"admin_dashboard","products":"admin_products","categories":"admin_categories","prices":"admin_prices","inventory":"admin_inventory","orders":"admin_orders","completed":"admin_completed_orders","customers":"admin_customers","website":"admin_website","offers":"admin_offers","reports":"admin_reports","settings":"admin_settings"}
    if section not in endpoints:abort(404)
    return redirect(url_for(endpoints[section]))


@app.get("/admin")
@app.get("/admin/")
@admin_required
def admin_dashboard():
    db=connect_db()
    metrics={"products":db.execute("SELECT COUNT(*) FROM products").fetchone()[0],"customers":db.execute("SELECT COUNT(*) FROM customers").fetchone()[0],
             "orders":db.execute("SELECT COUNT(*) FROM orders").fetchone()[0],"revenue":db.execute("SELECT COALESCE(SUM(total),0) FROM orders WHERE status!='Cancelled'").fetchone()[0],
             "pending":db.execute("SELECT COUNT(*) FROM orders WHERE status='Pending'").fetchone()[0]}
    recent=db.execute("SELECT id,customer_name,total,status,created_at FROM orders ORDER BY id DESC LIMIT 8").fetchall()
    return admin_render("overview",metrics=metrics,recent=recent)


@app.route("/admin/products", methods=["GET","POST"])
@admin_required
def admin_products():
    db=connect_db()
    if request.method=="POST":
        name=request.form.get("name","").strip()[:120];slug=re.sub(r"[^a-z0-9]+","-",request.form.get("slug",name).lower()).strip("-")[:80]
        description=request.form.get("description","").strip()[:2000]
        try:
            price=parse_price(request.form.get("price"),"price");category_id=int(request.form.get("category_id") or 0)
            sizes={}
            for line in request.form.get("sizes","Standard=0").splitlines():
                if "=" in line:
                    label,extra=line.split("=",1);label=label.strip()[:50]
                    if label:sizes[label]=parse_price(extra.strip(),"size price")
            colors=[color.strip().lower() for color in request.form.get("colors","").split(",") if color.strip()]
            if any(color not in {"white","black","navy","red","blue","pink","yellow"} for color in colors):raise ValueError("Use supported colours: white, black, navy, red, blue, pink or yellow.")
            if not name or not slug or not description or not sizes: raise ValueError("Name, slug, description and at least one size are required.")
            if category_id and not db.execute("SELECT 1 FROM categories WHERE id=?", (category_id,)).fetchone():
                raise ValueError("Choose an existing category.")
            inventory = int(request.form.get("inventory", 0))
            if inventory < 0:
                raise ValueError("Initial stock cannot be negative.")
            photo_print_areas = parse_photo_print_areas(request.form.get("photo_print_areas", "{}"), slug)
            photos = save_product_photo_views(request.files, {}, request.form.getlist("replace_photo_views"))
            model = save_licensed_model(request.files.get("model"), slug, request.form.get("model_license", ""))
            surfaces = parse_print_surfaces(request.form.get("print_surfaces", "{}"), slug)
            db.execute("INSERT INTO products(slug,name,price,description,category_id,enabled,sizes_json,colors_json,inventory,printing_charge,design_charge,discount_percent,featured,photo_views_json,model_file,model_license,print_surfaces_json,photo_print_areas_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (slug,name,price,description,category_id or None,1,json.dumps(sizes),json.dumps(colors),inventory,setting_price("printing_charge",PRINTING_CHARGE),setting_price("design_charge"),0,0,json.dumps(photos),model or "",request.form.get("model_license", "").strip() if model else "",json.dumps(surfaces),json.dumps(photo_print_areas)))
            image=save_product_image(request.files.get("image"))
            if image:db.execute("UPDATE products SET image=? WHERE slug=?",(image,slug))
            db.commit()
            flash("Product created successfully and added to the catalog.","success")
        except ValueError as error:
            db.rollback()
            flash(str(error),"error")
        except sqlite3.IntegrityError:
            db.rollback()
            duplicate=db.execute("SELECT 1 FROM products WHERE slug=?",(slug,)).fetchone() is not None
            message="That product slug already exists. Choose a different slug." if duplicate else "The database rejected this product because one of its values conflicts with a constraint. No product was added."
            flash(message,"error")
        except sqlite3.DatabaseError:
            db.rollback()
            app.logger.exception("Database error while creating an admin product")
            flash("A database error prevented this product from being saved. No product was added; please try again.","error")
        except OSError:
            db.rollback()
            app.logger.exception("File storage error while creating an admin product")
            flash("The product image could not be saved. Check the image and storage permissions, then try again.","error")
        return redirect(url_for("admin_products"))
    products=admin_product_rows(db)
    categories=db.execute("SELECT * FROM categories ORDER BY name").fetchall()
    return admin_render("products",products=products,categories=categories)


@app.route("/admin/products/<slug>", methods=["POST"])
@admin_required
def admin_product_edit(slug):
    db=connect_db();product=db.execute("SELECT * FROM products WHERE slug=?",(slug,)).fetchone()
    if product is None:abort(404)
    action=request.form.get("action","save")
    if action=="delete":
        photo_paths=[product["image"]] if product["image"] else []
        try:
            old_views=json.loads(product["photo_views_json"] or "{}")
            photo_paths.extend(path for group in old_views.values() if isinstance(group,list) for path in group)
        except (ValueError,TypeError,AttributeError):pass
        old_models=[product["model_file"]] if product["model_file"] else []
        db.execute("DELETE FROM products WHERE slug=?",(slug,));db.commit();delete_unused_product_assets(db,photo_paths,old_models);flash("Product deleted.","success");return redirect(url_for("admin_products"))
    try:
        name=request.form.get("name","").strip()[:120];description=request.form.get("description","").strip()[:2000]
        if not name or not description:raise ValueError("Product name and description are required.")
        price=parse_price(request.form.get("price"),"price")
        sizes={}
        if "sizes" not in request.form:
            sizes=dict(sizes_for_product(product))
        else:
            for line in request.form.get("sizes","").splitlines():
                if "=" in line:
                    label,extra=line.split("=",1);label=label.strip()[:50]
                    if label:sizes[label]=parse_price(extra.strip(),"size price")
        if not sizes:raise ValueError("Enter at least one size variant.")
        if "colors" not in request.form:
            try:
                colors=json.loads(product["colors_json"] or "[]")
                if not isinstance(colors,list):colors=[]
            except (ValueError,TypeError):
                colors=[]
        else:
            colors=[color.strip().lower() for color in request.form.get("colors","").split(",") if color.strip()]
        if any(color not in {"white","black","navy","red","blue","pink","yellow"} for color in colors):raise ValueError("Use supported colours: white, black, navy, red, blue, pink or yellow.")
        inventory=max(0,int(request.form.get("inventory","0")))
        printing=parse_price(request.form.get("printing_charge",setting_price("printing_charge",PRINTING_CHARGE)),"printing charge")
        design=parse_price(request.form.get("design_charge",0),"design charge")
        discount=parse_price(request.form.get("discount_percent",0),"discount percentage",100)
        category_id=int(request.form.get("category_id") or 0)
        if category_id and not db.execute("SELECT 1 FROM categories WHERE id=?",(category_id,)).fetchone():raise ValueError("Choose an existing category.")
        old_photo_paths=[product["image"]] if product["image"] else []
        try:
            old_views=json.loads(product["photo_views_json"] or "{}")
            old_photo_paths.extend(path for group in old_views.values() if isinstance(group,list) for path in group)
        except (ValueError,TypeError,AttributeError):pass
        old_model=product["model_file"] or ""
        photo_print_areas = parse_photo_print_areas(request.form.get("photo_print_areas", product["photo_print_areas_json"] or "{}"), slug)
        image=save_product_image(request.files.get("image"))
        try:
            current_photos = json.loads(product["photo_views_json"] or "{}")
        except (ValueError, TypeError):
            current_photos = {}
        photos = save_product_photo_views(request.files, current_photos, request.form.getlist("replace_photo_views"))
        model_license = request.form.get("model_license", product["model_license"] or "").strip()
        uploaded_model = request.files.get("model")
        model = save_licensed_model(uploaded_model, slug, model_license) if uploaded_model and uploaded_model.filename else None
        surfaces = parse_print_surfaces(request.form.get("print_surfaces", product["print_surfaces_json"] or "{}"), slug)
        db.execute("UPDATE products SET name=?,description=?,price=?,category_id=?,sizes_json=?,colors_json=?,inventory=?,printing_charge=?,design_charge=?,discount_percent=?,enabled=?,featured=?,image=CASE WHEN ? IS NULL THEN image ELSE ? END,photo_views_json=?,model_file=CASE WHEN ? IS NULL THEN model_file ELSE ? END,model_license=CASE WHEN ? IS NULL THEN model_license ELSE ? END,print_surfaces_json=?,photo_print_areas_json=? WHERE slug=?",
                   (name,description,price,category_id or None,json.dumps(sizes),json.dumps(colors),inventory,printing,design,discount,int(request.form.get("enabled")=="on"),int(request.form.get("featured")=="on"),image,image,json.dumps(photos),model,model,model_license if model else None,model_license if model else None,json.dumps(surfaces),json.dumps(photo_print_areas),slug))
        db.commit()
        delete_unused_product_assets(db,old_photo_paths,([old_model] if old_model and model and model!=old_model else []))
        if image and product["image"] and image!=product["image"]:delete_unused_product_assets(db,[product["image"]])
        flash("Product updated.","success")
    except (ValueError,TypeError) as error:
        db.rollback();flash(str(error) or "Check product fields.","error")
    return redirect(url_for("admin_products"))


@app.route("/admin/categories", methods=["GET","POST"])
@admin_required
def admin_categories():
    db=connect_db()
    if request.method=="POST":
        action=request.form.get("action","add")
        if action=="delete":
            category_id=request.form.get("category_id",type=int)
            db.execute("UPDATE products SET category_id=NULL WHERE category_id=?",(category_id,));db.execute("DELETE FROM categories WHERE id=?",(category_id,));db.commit();flash("Category removed.","success")
        else:
            name=request.form.get("name","").strip()[:80]
            if not name:flash("Enter a category name.","error")
            else:
                try:db.execute("INSERT INTO categories(name) VALUES(?)",(name,));db.commit();flash("Category added.","success")
                except sqlite3.IntegrityError:flash("That category already exists.","error")
        return redirect(url_for("admin_categories"))
    return admin_render("categories",categories=db.execute("SELECT c.*,COUNT(p.slug) product_count FROM categories c LEFT JOIN products p ON p.category_id=c.id GROUP BY c.id ORDER BY c.name").fetchall())


@app.route("/admin/prices", methods=["GET","POST"])
@admin_required
def admin_prices():
    db=connect_db()
    if request.method=="POST":
        try:
            values={key:parse_price(request.form.get(key),label) for key,label in [("printing_charge","printing charge"),("design_charge","design charge"),("delivery_charge","delivery charge")]}
            for key,value in values.items():db.execute("INSERT INTO site_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,str(value)))
            db.execute("UPDATE products SET printing_charge=?,design_charge=?",(values["printing_charge"],values["design_charge"]))
            db.commit();flash("Shared printing, design and delivery charges updated.","success")
        except ValueError as error:db.rollback();flash(str(error),"error")
        return redirect(url_for("admin_prices"))
    return admin_render("prices",settings=settings_map(),products=db.execute("SELECT slug,name,price,printing_charge,design_charge,discount_percent FROM products ORDER BY name").fetchall())


@app.route("/admin/inventory", methods=["GET","POST"])
@admin_required
def admin_inventory():
    db=connect_db()
    if request.method=="POST":
        try:db.execute("UPDATE products SET inventory=? WHERE slug=?",(max(0,int(request.form.get("inventory",0))),request.form.get("slug")));db.commit();flash("Inventory saved.","success")
        except (ValueError,TypeError):db.rollback();flash("Enter a valid stock quantity.","error")
        return redirect(url_for("admin_inventory"))
    return admin_render("inventory",products=db.execute("SELECT slug,name,inventory,enabled FROM products ORDER BY name").fetchall())


ORDER_STATUSES={"Pending","Confirmed","Processing","Shipped","Delivered","Cancelled"}
@app.get("/admin/orders")
@admin_required
def admin_orders():
    query=request.args.get("q","").strip()[:100];status=request.args.get("status","")
    sql="SELECT id,customer_name,email,phone,total,status,created_at,delivered_at FROM orders WHERE 1=1";params=[]
    if query:sql+=" AND (customer_name LIKE ? OR email LIKE ? OR CAST(id AS TEXT)=?)";params.extend([f"%{query}%",f"%{query}%",query])
    if status in ORDER_STATUSES:sql+=" AND status=?";params.append(status)
    sql+=" ORDER BY id DESC"
    return admin_render("orders",orders=connect_db().execute(sql,params).fetchall(),query=query,status=status,statuses=sorted(ORDER_STATUSES))


@app.get("/admin/completed-orders")
@admin_required
def admin_completed_orders():
    rows=connect_db().execute("SELECT id,customer_name,email,phone,address,total,status,created_at,delivered_at,items_json FROM orders WHERE status='Delivered' ORDER BY delivered_at DESC,id DESC").fetchall()
    orders=[]
    for row in rows:
        order=dict(row)
        try:
            items=json.loads(order["items_json"] or "[]")
        except (ValueError,TypeError):
            items=[]
        order["products_summary"] = ", ".join(
            f"{item.get('name','Product')} × {item.get('quantity',1)}" for item in items if isinstance(item,dict)
        )
        orders.append(order)
    return admin_render("completed",orders=orders)


@app.route("/admin/orders/<int:order_id>",methods=["GET","POST"])
@admin_required
def admin_order_detail(order_id):
    db=connect_db();order=db.execute("SELECT * FROM orders WHERE id=?",(order_id,)).fetchone()
    if order is None:abort(404)
    if request.method=="POST":
        status=request.form.get("status")
        if status not in ORDER_STATUSES:abort(400,"Invalid order status.")
        db.execute("BEGIN IMMEDIATE")
        current=db.execute("SELECT status,customer_id,delivered_at FROM orders WHERE id=?",(order_id,)).fetchone()
        if current is None:
            db.rollback();abort(404)
        if current["status"]=="Delivered" and status!="Delivered":
            db.rollback();flash("A delivered order is complete and cannot be moved back to an earlier status.","error")
            return redirect(url_for("admin_order_detail",order_id=order_id))
        if current["status"]=="Delivered" and status=="Delivered":
            db.rollback();flash("This order is already completed. No duplicate completion notification was created.","info")
            return redirect(url_for("admin_order_detail",order_id=order_id))
        if status=="Delivered":
            delivered_at=datetime.now().astimezone().isoformat(timespec="seconds")
            db.execute("UPDATE orders SET status=?,delivered_at=? WHERE id=?",(status,delivered_at,order_id))
            if current["customer_id"] is not None:
                message=f"Your order #{order_id} has been successfully delivered. Thank you for shopping with ANSUMAN GRAPHIC!"
                db.execute("INSERT OR IGNORE INTO customer_notifications(customer_id,order_id,notification_type,message,created_at) VALUES(?,?,?,?,?)",
                           (current["customer_id"],order_id,"order_delivered",message,delivered_at))
        else:
            db.execute("UPDATE orders SET status=? WHERE id=?",(status,order_id))
        db.commit();flash("Order marked delivered and customer notified." if status=="Delivered" else "Order status updated.","success")
        return redirect(url_for("admin_order_detail",order_id=order_id))
    try:
        items=json.loads(order["items_json"] or "[]")
    except (ValueError, TypeError):
        items=[]
    for item in items:
        if not isinstance(item, dict):
            continue
        item["print_groups"]=[]
        for surface, layers in group_artworks(item).items():
            location,view=surface.rsplit("::",1)
            available = True
            for layer in layers:
                try:
                    name=artwork_filename(layer)
                    path=(UPLOAD_DIR / name).resolve()
                    layer["source_available"]=path.parent == UPLOAD_DIR.resolve() and path.is_file()
                    if not layer["source_available"] and not isinstance(layer.get("text_spec"),dict):
                        available=False
                except (MissingPrintAsset, OSError):
                    layer["source_available"]=False
                    if not isinstance(layer.get("text_spec"),dict):
                        available=False
            try:
                width,height,dpi=print_spec(item,location)
            except ValueError:
                width=height=dpi=0
            design=item.get("design") if isinstance(item.get("design"),dict) else {}
            design_id=design.get("design_id") or item.get("design_id") or item.get("id")
            label=dict(PRINT_AREAS.get(item.get("slug"),[])).get(location,location.replace("_"," ").title())
            item["print_groups"].append({
                "surface":surface,"location":location,"label":label,"layers":layers,"available":available,
                "width":width,"height":height,"dpi":dpi,"design_id":design_id,
                "view":view,
            })
    return render_template("admin_order_detail.html",order=order,items=items,statuses=sorted(ORDER_STATUSES),sections=ADMIN_SECTIONS)


def item_design_snapshot(item):
    design=item.get("design")
    return design if isinstance(design,dict) else {}


def item_design_id(item):
    return item_design_snapshot(item).get("design_id") or item.get("design_id") or item.get("id")


def order_artwork_layer(order_id, design_id, filename):
    order=connect_db().execute("SELECT items_json FROM orders WHERE id=?",(order_id,)).fetchone()
    if order is None:
        abort(404)
    try:
        items=json.loads(order["items_json"] or "[]")
    except (ValueError,TypeError):
        abort(404)
    for item in items:
        if not isinstance(item,dict):
            continue
        if item_design_id(item) != design_id:
            continue
        for artwork in item.get("artworks",[]):
            try:
                if artwork_filename(artwork) != filename:
                    continue
                path=(UPLOAD_DIR / filename).resolve()
                if path.parent != UPLOAD_DIR.resolve() or not path.is_file():
                    abort(404,"The original customer artwork is missing from storage.")
                return artwork
            except MissingPrintAsset:
                continue
    abort(404)


@app.get("/admin/orders/<int:order_id>/artwork/<design_id>/<filename>")
@admin_required
def admin_order_artwork(order_id,design_id,filename):
    artwork=order_artwork_layer(order_id,design_id,filename)
    path=(UPLOAD_DIR / filename).resolve()
    response=send_file(path,as_attachment=True,download_name=artwork.get("original_name") or filename,
                       mimetype=artwork.get("content_type") or "application/octet-stream",max_age=0)
    response.headers["Cache-Control"]="private, no-store"
    response.headers["X-Content-Type-Options"]="nosniff"
    return response


@app.get("/admin/orders/<int:order_id>/print/<design_id>/<location>/<view>")
@admin_required
def admin_order_print(order_id,design_id,location,view):
    order=connect_db().execute("SELECT items_json FROM orders WHERE id=?",(order_id,)).fetchone()
    if order is None:
        abort(404)
    try:
        items=json.loads(order["items_json"] or "[]")
    except (ValueError,TypeError):
        abort(404)
    item=next((candidate for candidate in items if isinstance(candidate,dict) and item_design_id(candidate)==design_id),None)
    if item is None:
        abort(404)
    try:
        if view not in {"front","back","left","right","side","detail"}:
            abort(404)
        payload,spec=render_print_png(item,location,UPLOAD_DIR,view)
    except MissingPrintAsset as error:
        abort(404,str(error))
    except ValueError as error:
        abort(404,str(error))
    filename=f"order-{order_id}-{item.get('slug','product')}-{location}-{view}.png"
    response=send_file(BytesIO(payload),mimetype="image/png",as_attachment=request.args.get("inline")!="1",
                       download_name=filename,max_age=0,conditional=False)
    response.headers["Cache-Control"]="private, no-store"
    response.headers["X-Content-Type-Options"]="nosniff"
    return response


@app.get("/admin/customers")
@admin_required
def admin_customers():
    q=request.args.get("q","").strip()[:100]
    rows=connect_db().execute("SELECT c.id,c.name,c.email,c.phone,c.created_at,COUNT(o.id) order_count FROM customers c LEFT JOIN orders o ON o.customer_id=c.id WHERE c.name LIKE ? OR c.email LIKE ? OR c.phone LIKE ? GROUP BY c.id ORDER BY c.id DESC",(f"%{q}%",f"%{q}%",f"%{q}%")).fetchall()
    return admin_render("customers",customers=rows,query=q)


@app.route("/admin/website",methods=["GET","POST"])
@admin_required
def admin_website():
    db=connect_db()
    if request.method=="POST":
        action=request.form.get("action", "")
        if action in {"banner_add", "banner_update", "banner_delete"}:
            settings=settings_map()
            banners=homepage_banners_from_settings(settings)
            banner_id=request.form.get("banner_id", "").strip()
            try:
                if action=="banner_add":
                    if len(banners)>=20:raise ValueError("You can display up to 20 homepage promotional banners.")
                    title=request.form.get("banner_title", "").strip()[:120]
                    if not title:raise ValueError("Enter a title for the promotional banner.")
                    target_url=validate_homepage_banner_url(request.form.get("banner_url", ""))
                    image=save_product_image(request.files.get("promo_banner_image"))
                    if not image:raise ValueError("Choose a banner image to add.")
                    banners.append({"id":secrets.token_hex(8),"title":title,"url":target_url,"image":image})
                    message="Promotional banner added. The Print / Customize section is unchanged."
                elif action=="banner_update":
                    banner=next((item for item in banners if item["id"]==banner_id), None)
                    if banner is None:raise ValueError("That promotional banner no longer exists. Refresh and try again.")
                    title=request.form.get("banner_title", "").strip()[:120]
                    if not title:raise ValueError("Enter a title for the promotional banner.")
                    target_url=validate_homepage_banner_url(request.form.get("banner_url", ""))
                    image=save_product_image(request.files.get("promo_banner_image"))
                    banner.update({"title":title,"url":target_url})
                    if image:banner["image"]=image
                    message="Promotional banner updated. Other homepage content is unchanged."
                else:
                    remaining=[item for item in banners if item["id"]!=banner_id]
                    if len(remaining)==len(banners):raise ValueError("That promotional banner no longer exists. Refresh and try again.")
                    banners=remaining
                    message="Promotional banner removed from the homepage. Its image file was retained."
                db.execute("INSERT INTO site_settings(key,value) VALUES('homepage_banners',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps(banners,separators=(",",":")),))
                db.commit();flash(message,"success")
            except ValueError as error:
                db.rollback();flash(str(error),"error")
            return redirect(url_for("admin_website"))
        if action=="product_flags":
            for product in db.execute("SELECT slug FROM products").fetchall():
                slug=product["slug"]
                db.execute("UPDATE products SET enabled=?,featured=? WHERE slug=?",(int(slug in request.form.getlist("visible")),int(slug in request.form.getlist("featured")),slug))
            db.commit();flash("Featured products and visibility saved.","success");return redirect(url_for("admin_website"))
        for key in ("hero_title","hero_subtitle","promo_text"):
            value=request.form.get(key,"").strip()[:300]
            db.execute("INSERT INTO site_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,value))
        try:
            image=save_product_image(request.files.get("banner_image"))
            if image:db.execute("INSERT INTO site_settings(key,value) VALUES('banner_image',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(image,))
            db.commit();flash("Homepage content saved.","success")
        except ValueError as error:db.rollback();flash(str(error),"error")
        return redirect(url_for("admin_website"))
    settings=settings_map()
    return admin_render("website",settings=settings,homepage_banners=homepage_banners_from_settings(settings),products=connect_db().execute("SELECT slug,name,featured,enabled FROM products ORDER BY name").fetchall())


@app.route("/admin/offers",methods=["GET","POST"])
@admin_required
def admin_offers():
    db=connect_db()
    if request.method=="POST":
        try:discount=parse_price(request.form.get("discount_percent"),"discount percentage",100)
        except ValueError as error:flash(str(error),"error")
        else:db.execute("UPDATE products SET discount_percent=? WHERE slug=?",(discount,request.form.get("slug")));db.commit();flash("Offer updated.","success")
        return redirect(url_for("admin_offers"))
    return admin_render("offers",products=db.execute("SELECT slug,name,price,discount_percent,enabled FROM products ORDER BY name").fetchall())


@app.get("/admin/reports")
@admin_required
def admin_reports():
    db=connect_db()
    rows=db.execute("SELECT substr(created_at,1,7) month,COUNT(*) orders,SUM(CASE WHEN status!='Cancelled' THEN total ELSE 0 END) revenue FROM orders GROUP BY month ORDER BY month DESC LIMIT 24").fetchall()
    return admin_render("reports",reports=rows)


@app.route("/admin/settings",methods=["GET","POST"])
@admin_required
def admin_settings():
    db=connect_db()
    if request.method=="POST":
        site_name=request.form.get("site_name","ANSUMAN GRAPHIC").strip()[:100]
        support_email=request.form.get("support_email","").strip()[:254]
        support_phone=request.form.get("support_phone","").strip()[:32]
        for key,value in (("site_name",site_name),("support_email",support_email),("support_phone",support_phone)):
            db.execute("INSERT INTO site_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,value))
        db.commit();flash("Website settings saved.","success");return redirect(url_for("admin_settings"))
    return admin_render("settings",settings=settings_map())


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    safe_path=Path(filename)
    if safe_path.name!=filename or ".." in safe_path.parts:abort(404)
    allowed={item.get("image") for item in session.get("cart",[])}
    allowed.update(artwork.get("image") for item in session.get("cart",[]) for artwork in item.get("artworks",[]))
    role=session.get("role")
    if filename not in allowed and role!="admin":
        if role=="customer":
            rows=connect_db().execute("SELECT items_json FROM orders WHERE customer_id=?",(session.get("customer_id"),)).fetchall()
            for row in rows:
                for item in json.loads(row["items_json"]):
                    if any(art.get("image")==filename for art in item.get("artworks",[])):allowed.add(filename)
        if filename not in allowed:abort(404)
    response=send_from_directory(UPLOAD_DIR, filename)
    response.headers["Cache-Control"]="private, no-store"
    response.headers["X-Content-Type-Options"]="nosniff"
    return response


if __name__ == "__main__":
    if IS_RENDER:
        raise SystemExit("Render production must use Gunicorn: gunicorn --bind 0.0.0.0:$PORT app:app")
    app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "5000")),
        debug=development_debug_enabled(),
    )
