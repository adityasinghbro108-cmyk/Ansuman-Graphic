import io
import secrets
import json
import re
import sqlite3
import tempfile
import unittest
from contextlib import closing
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import app as website
import studio_assistant


class MarkupAudit(HTMLParser):
    void_tags = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids = []
        self.stack = []
        self.errors = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(values["id"])
        if tag not in self.void_tags:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.void_tags and self.stack and self.stack[-1] == tag:
            self.stack.pop()

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"unexpected closing tag: {tag}")
        else:
            self.stack.pop()


class StudioFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db_patch = patch.object(website, "DATABASE", self.root / "studio-test.sqlite3")
        self.upload_patch = patch.object(website, "UPLOAD_DIR", self.root / "uploads")
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.upload_patch.start()
        self.addCleanup(self.upload_patch.stop)
        website.initialize_database()
        website.app.config.update(TESTING=True)
        self.client_context = website.app.test_client()
        self.client = self.client_context.__enter__()
        self.addCleanup(self.client_context.__exit__, None, None, None)

    def request(self, method, path, **kwargs):
        if method.upper() not in {"GET", "HEAD", "OPTIONS"}:
            with self.client.session_transaction() as session:
                token = session.setdefault("_csrf_token", secrets.token_urlsafe(32))
            headers = dict(kwargs.get("headers", {})); headers.setdefault("X-CSRF-Token", token); kwargs["headers"] = headers
            if isinstance(kwargs.get("data"), dict): kwargs["data"].setdefault("csrf_token", token)
        response = self.client.open(path, method=method, **kwargs)
        self.addCleanup(response.close)
        return response

    @staticmethod
    def png(color):
        stream = io.BytesIO()
        image = Image.new("RGB", (120, 100), color)
        try:
            image.save(stream, "PNG")
        finally:
            image.close()
        stream.seek(0)
        return stream

    @staticmethod
    def webp(color, size=(1800, 1200)):
        stream = io.BytesIO()
        image = Image.new("RGB", size, color)
        try:
            image.save(stream, "WEBP", quality=93)
        finally:
            image.close()
        stream.seek(0)
        return stream

    def test_missing_ai_credentials_report_setup_instead_of_demo(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": ""}):
            response = self.request("POST", "/api/design-assistant", json={
                "message": "Put my photo on the left chest", "product": "tshirt", "image_count": 1,
            })
        self.assertEqual(response.status_code, 503)
        payload = response.get_json()
        self.assertFalse(payload["success"])
        self.assertIn("OPENAI_API_KEY", payload["message"])
        self.assertIn("No design changes were made", payload["message"])

    def test_ai_action_validation_allows_supported_edits_and_rejects_invalid(self):
        context = {"product": "tshirt", "image_count": 2, "available_colors": ["white", "black", "navy", "red", "blue", "pink", "yellow"], "has_text_selection": True}
        actions = [
            {"action":"add_uploaded_image", "index":1},
            {"action":"set_location", "location":"left_chest"},
            {"action":"move_artwork", "placement":"upper_center", "x":.1, "y":-.2},
            {"action":"resize_artwork", "scale":.65},
            {"action":"rotate_artwork", "angle":28},
            {"action":"add_text", "text":"Happy Birthday", "color":"red", "font":"Impact"},
            {"action":"set_text_style", "color":"blue", "font":"Georgia"},
            {"action":"set_view", "view":"back"},
            {"action":"undo"}, {"action":"redo"}, {"action":"reset_design"},
            {"action":"move_artwork", "placement":"center", "x":9, "y":0},
            {"action":"resize_artwork", "scale":20},
            {"action":"rotate_artwork", "angle":999},
            {"action":"set_location", "location":"left_sleeve", "extra":"ignored"},
            {"action":"run_javascript", "code":"alert(1)"},
            {"action":"add_text", "text":"x"*81},
        ]
        clean = studio_assistant._clean_actions(actions, context, {slug:{key for key,_ in areas} for slug,areas in website.PRINT_AREAS.items()})
        self.assertEqual([action["action"] for action in clean], ["add_uploaded_image", "set_location", "move_artwork", "resize_artwork", "rotate_artwork", "add_text", "set_text_style", "set_view", "undo", "redo", "reset_design"])
        self.assertEqual(clean[5]["text"], "Happy Birthday")
        self.assertEqual(clean[5]["font"], "Impact")
        no_image = studio_assistant._clean_actions([{"action":"place_artwork", "placement":"center"}], {**context,"image_count":0}, {})
        self.assertEqual(no_image, [])

    def test_ai_provider_response_is_structured_and_sanitized(self):
        result = {"reply":"I moved the image.","actions":[{"action":"move_artwork","product":None,"location":None,"placement":"left_chest","color":None,"text":None,"view":None,"index":None,"x":0.1,"y":0.05,"scale":None,"angle":None,"font":None},{"action":"run_javascript","product":None,"location":None,"placement":None,"color":None,"text":None,"view":None,"index":None,"x":None,"y":None,"scale":None,"angle":None,"font":None}]}
        body = {"output":[{"type":"function_call","name":"apply_studio_actions","arguments":json.dumps(result)}]}
        class FakeResponse(io.BytesIO):
            pass
        captured = {}
        def fake_urlopen(request, timeout):
            captured["request"] = request; captured["timeout"] = timeout
            return FakeResponse(json.dumps(body).encode("utf-8"))
        with patch.dict("os.environ", {"OPENAI_API_KEY":"test-server-key"}), patch.object(studio_assistant,"urlopen",side_effect=fake_urlopen):
            response = self.request("POST", "/api/design-assistant", json={
                "message":"Move it left", "product":"tshirt", "image_count":1,
                "conversation":[{"role":"user","content":"Earlier request"}],
            })
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        payload=response.get_json(); self.assertEqual(payload["mode"],"ai")
        self.assertEqual(payload["actions"],[{"action":"move_artwork","placement":"left_chest","x":.1,"y":.05}])
        self.assertEqual(captured["timeout"],20)
        self.assertEqual(captured["request"].get_header("Authorization"),"Bearer test-server-key")
        self.assertNotIn(b"test-server-key", response.data)
        request_body=json.loads(captured["request"].data)
        self.assertEqual(request_body["input"][0]["content"],"Earlier request")

    def test_print_location_is_preserved_through_cart_and_order(self):
        response = self.request("POST", "/cart/add", headers={
            "X-Requested-With": "XMLHttpRequest", "Accept": "application/json",
        }, data={
            "product": "tshirt", "size": "M", "quantity": "1",
            "artwork_settings": json.dumps([{"location": "left_chest", "scale": 1.1, "rotation": 8}]),
            "design_images": [(self.png("blue"), "chest.png")],
        }, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        with self.client.session_transaction() as session:
            self.assertEqual(session["cart"][0]["artworks"][0]["location"], "left_chest")
        cart = self.request("GET", "/cart")
        self.assertIn(b"Left chest", cart.data)
        checkout = self.request("POST", "/checkout", data={
            "customer_name": "Print Area", "email": "area@example.com",
            "phone": "1234567890", "address": "Test address",
        })
        self.assertEqual(checkout.status_code, 200)
        with closing(sqlite3.connect(website.DATABASE)) as db:
            saved = json.loads(db.execute("SELECT items_json FROM orders").fetchone()[0])
        self.assertEqual(saved[0]["artworks"][0]["location"], "left_chest")

    def test_photo_view_is_preserved_per_design_layer_in_cart_snapshot(self):
        response=self.request("POST","/cart/add",headers={"X-Requested-With":"XMLHttpRequest","Accept":"application/json"},data={
            "product":"tshirt","size":"M","quantity":"1",
            "design_snapshot":json.dumps({"view":"back","product":"tshirt","variant":"M"}),
            "artwork_settings":json.dumps([{"location":"back_center","view":"back","scale":1,"rotation":0}]),
            "design_images":[(self.png("navy"),"back-art.png")],
        },content_type="multipart/form-data")
        self.assertEqual(response.status_code,201,response.get_data(as_text=True))
        with self.client.session_transaction() as session:
            saved=session["cart"][-1]
        self.assertEqual(saved["design"]["view"],"back")
        self.assertEqual(saved["design"]["layers"][0]["view"],"back")

    def test_text_layer_style_and_placement_persist_through_cart_checkout(self):
        settings = [{
            "location": "back_center", "scale": 1.25, "rotation": -17,
            "offset_x": 11, "offset_y": -8,
            "text_spec": {"text": "Team AnsumAN", "font": "Georgia", "color": "purple",
                          "size": 125, "bold": True, "italic": True},
        }]
        response = self.request("POST", "/cart/add", data={
            "product": "tshirt", "size": "L", "quantity": "2", "color": "navy",
            "artwork_settings": json.dumps(settings),
            "design_images": [(self.png("purple"), "studio-text.png")],
        }, content_type="multipart/form-data", follow_redirects=True)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        with self.client.session_transaction() as session:
            item = session["cart"][0]
            self.assertEqual(item["color"], "navy")
            self.assertEqual(item["artworks"][0]["location"], "back_center")
            self.assertEqual(item["artworks"][0]["rotation"], -17)
        self.assertEqual(item["artworks"][0]["text_spec"], settings[0]["text_spec"] | {"alignment":"center"})
        checkout = self.request("POST", "/checkout", data={
            "customer_name": "Text Layer", "email": "text-layer@example.com",
            "phone": "1234567890", "address": "Text layer address",
        })
        self.assertEqual(checkout.status_code, 200)
        with closing(sqlite3.connect(website.DATABASE)) as db:
            saved = json.loads(db.execute("SELECT items_json FROM orders").fetchone()[0])
        self.assertEqual(saved[0]["artworks"][0]["text_spec"], settings[0]["text_spec"] | {"alignment":"center"})

        invalid = self.request("POST", "/cart/add", data={
            "product": "tshirt", "size": "M", "quantity": "1",
            "artwork_settings": json.dumps([{"text_spec": {**settings[0]["text_spec"], "font": "Comic Script"}}]),
            "design_images": [(self.png("red"), "invalid-text-style.png")],
        }, content_type="multipart/form-data", follow_redirects=True)
        self.assertEqual(invalid.status_code, 200)
        self.assertIn(b"supported text font", invalid.data)

    def test_csrf_is_required_on_customer_and_admin_mutations(self):
        response = self.client.post("/register", data={
            "name":"No Token", "email":"no-token@example.com", "phone":"1234567890", "password":"long-password-123",
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"form session expired", response.data.lower())

    def test_customer_registration_login_profile_and_private_orders(self):
        registered = self.request("POST", "/register", data={
            "name":"Customer One", "email":"one@example.com", "phone":"1112223333", "password":"customer-secret-123",
        })
        self.assertEqual(registered.status_code, 302)
        duplicate = self.request("POST", "/register", data={
            "name":"Customer One", "email":"ONE@example.com", "phone":"1112223333", "password":"customer-secret-123",
        })
        self.assertEqual(duplicate.status_code, 409)
        with closing(sqlite3.connect(website.DATABASE)) as db:
            customer = db.execute("SELECT id,password_hash FROM customers WHERE email=?",("one@example.com",)).fetchone()
        self.assertNotEqual(customer[1], "customer-secret-123")
        logged_in = self.request("POST", "/login", data={"email":"one@example.com","password":"customer-secret-123"})
        self.assertEqual(logged_in.status_code, 302)
        self.assertEqual(self.request("GET", "/account/profile").status_code, 200)
        self.assertEqual(self.request("GET", "/admin/products").status_code, 302)
        edited = self.request("POST", "/account/profile", data={"name":"Customer Updated","email":"one@example.com","phone":"9998887777"})
        self.assertEqual(edited.status_code, 200)

        added = self.request("POST", "/cart/add", headers={"X-Requested-With":"XMLHttpRequest"}, data={
            "product":"mug","size":"11 oz","quantity":"1","design_images":[(self.png("blue"),"customer.png")],
        },content_type="multipart/form-data")
        self.assertEqual(added.status_code,201,added.get_data(as_text=True))
        with self.client.session_transaction() as session:
            artwork_name=session["cart"][0]["artworks"][0]["image"]
        order_response=self.request("POST","/checkout",data={"customer_name":"Ignored","email":"ignored@example.com","phone":"000","address":"Customer home"})
        self.assertEqual(order_response.status_code,200)
        with closing(sqlite3.connect(website.DATABASE)) as db:
            order_id,owner=db.execute("SELECT id,customer_id FROM orders ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(owner,customer[0])
        self.assertEqual(self.request("GET",f"/account/orders/{order_id}").status_code,200)

        other_context=website.app.test_client()
        try:
            with other_context.session_transaction() as session:
                token=secrets.token_urlsafe(32);session["_csrf_token"]=token
            other_context.post("/register",data={"csrf_token":token,"name":"Customer Two","email":"two@example.com","phone":"2223334444","password":"customer-secret-456"})
            with other_context.session_transaction() as session:
                session["_csrf_token"]=token
            other_context.post("/login",data={"csrf_token":token,"email":"two@example.com","password":"customer-secret-456"})
            with other_context.session_transaction() as session:
                self.assertNotEqual(session.get("customer_id"),owner)
            private=other_context.get(f"/account/orders/{order_id}")
            self.assertEqual(private.status_code,404)
            private_file=other_context.get(f"/uploads/{artwork_name}")
            self.assertEqual(private_file.status_code,404)
        finally:
            other_context.__exit__(None,None,None)

    def create_admin(self):
        with closing(sqlite3.connect(website.DATABASE)) as db:
            db.execute("INSERT INTO admin_users(email,password_hash,created_at) VALUES(?,?,?)",("shop@example.com",website.generate_password_hash("separate-admin-secret-123"),"2026-01-01T00:00:00"))
            db.execute("INSERT INTO customers(name,email,phone,password_hash,created_at) VALUES(?,?,?,?,?)",("Shop Customer","shop@example.com","1112223333",website.generate_password_hash("customer-only-password-123"),"2026-01-01T00:00:00"))
            db.commit()

    def test_admin_login_redirects_and_product_create_edit_variants(self):
        self.create_admin()
        login_page=self.request("GET","/admin/login")
        self.assertIn(b"Admin Email / Admin ID",login_page.data)
        self.assertIn(b'class="admin-login-submit" type="submit"',login_page.data)
        self.assertIn(b"OPEN DASHBOARD",login_page.data)
        self.assertIn(b"admin-login-arrow",login_page.data)

        direct=self.request("POST","/admin/login",data={"email":"shop@example.com","password":"separate-admin-secret-123"})
        self.assertEqual(direct.status_code,302)
        self.assertEqual(direct.headers["Location"],"/admin")
        self.assertEqual(self.request("POST","/admin/logout").status_code,302)

        protected=self.request("GET","/admin/orders")
        self.assertEqual(protected.status_code,302)
        self.assertIn("/admin/login?next=/admin/orders",protected.headers["Location"])
        returned=self.request("POST","/admin/login",data={
            "email":"shop@example.com","password":"separate-admin-secret-123","next":"/admin/orders",
        })
        self.assertEqual(returned.status_code,302)
        self.assertEqual(returned.headers["Location"],"/admin/orders")

        product_form=self.request("GET","/admin/products")
        self.assertIn(b'method="post" action="/admin/products" enctype="multipart/form-data"',product_form.data)
        for field in (b'name="name"',b'name="slug"',b'name="price"',b'name="category_id"',b'name="description"',b'name="sizes"',b'name="colors"',b'name="inventory"',b'name="image"'):
            self.assertIn(field,product_form.data)
        self.assertIn(b'type="submit">Create product',product_form.data)

        created=self.request("POST","/admin/products",data={
            "name":"Travel Bottle","slug":"travel-bottle","description":"A steel bottle with custom print.",
            "price":"525","category_id":"1","sizes":"500 ml=0\n750 ml=80",
            "colors":"white, blue","inventory":"12",
        },follow_redirects=True)
        self.assertEqual(created.status_code,200)
        self.assertIn(b"Product created successfully and added to the catalog",created.data)
        with closing(sqlite3.connect(website.DATABASE)) as db:
            product=db.execute("SELECT name,price,description,category_id,enabled,sizes_json,colors_json,inventory,printing_charge,design_charge,discount_percent,featured FROM products WHERE slug='travel-bottle'").fetchone()
        self.assertIsNotNone(product)
        self.assertEqual(product[0:5],("Travel Bottle",525.0,"A steel bottle with custom print.",1,1))
        self.assertEqual(json.loads(product[5]),{"500 ml":0.0,"750 ml":80.0})
        self.assertEqual(json.loads(product[6]),["white","blue"])
        self.assertEqual(product[7],12)

        duplicate=self.request("POST","/admin/products",data={
            "name":"Duplicate bottle","slug":"travel-bottle","description":"duplicate test","price":"100",
            "category_id":"1","sizes":"Standard=0","colors":"white","inventory":"1",
        },follow_redirects=True)
        self.assertEqual(duplicate.status_code,200)
        self.assertIn(b"product slug already exists",duplicate.data)
        invalid=self.request("POST","/admin/products",data={
            "name":"","slug":"bad-product","description":"","price":"bad",
            "category_id":"999","sizes":"","colors":"purple","inventory":"invalid",
        },follow_redirects=True)
        self.assertEqual(invalid.status_code,200)
        self.assertIn(b"Enter a valid price",invalid.data)

        with closing(sqlite3.connect(website.DATABASE)) as db:
            existing=db.execute("SELECT sizes_json,colors_json FROM products WHERE slug='tshirt'").fetchone()
        edit_page=self.request("GET","/admin/products")
        self.assertIn(b"S=0\nM=0",edit_page.data)
        self.assertIn(b'name="colors" value="white, black, navy, red, blue, pink, yellow"',edit_page.data)
        saved=self.request("POST","/admin/products/tshirt",data={
            "name":"Custom T-Shirts","description":"Soft cotton. Made yours.","price":"599","category_id":"1",
            "inventory":"100","printing_charge":"99","design_charge":"0","discount_percent":"0","enabled":"on","featured":"on",
        })
        self.assertEqual(saved.status_code,302)
        with closing(sqlite3.connect(website.DATABASE)) as db:
            preserved=db.execute("SELECT sizes_json,colors_json FROM products WHERE slug='tshirt'").fetchone()
        self.assertEqual(json.loads(preserved[0]),json.loads(existing[0]))
        self.assertEqual(json.loads(preserved[1]),json.loads(existing[1]))

    def test_admin_product_photo_views_are_managed_and_publicly_configured(self):
        self.create_admin()
        self.request("POST","/admin/login",data={"email":"shop@example.com","password":"separate-admin-secret-123"})
        result=self.request("POST","/admin/products",data={
            "name":"Photo Tee","slug":"photo-tee","description":"A photo view test shirt.","price":"500",
            "category_id":"1","sizes":"M=0","colors":"white, navy","inventory":"10",
            "photos_front":(self.png("red"),"front.png"),"photos_back":(self.png("blue"),"back.png"),
            "print_surfaces":"{\"front_center\":\"PRINT_FRONT\",\"back_center\":\"PRINT_BACK\"}",
            "photo_print_areas":"{\"front\":{\"front\":[[0.35,0.28],[0.65,0.28],[0.65,0.62],[0.35,0.62]]}}",
        },follow_redirects=True)
        self.assertEqual(result.status_code,200)
        with closing(sqlite3.connect(website.DATABASE)) as db:
            saved_product=db.execute("SELECT photo_views_json,photo_print_areas_json FROM products WHERE slug='photo-tee'").fetchone()
        self.assertIsNotNone(saved_product,result.get_data(as_text=True))
        views=json.loads(saved_product[0]);photo_areas=json.loads(saved_product[1])
        self.assertEqual(set(views),{"front","back"})
        self.assertEqual(photo_areas["front"]["front"],[[.35,.28],[.65,.28],[.65,.62],[.35,.62]])
        self.assertEqual(website.studio_catalog()["photo-tee"]["photo_print_areas"],photo_areas)
        self.assertTrue((website.BASE_DIR/"static"/views["front"][0]).is_file())
        self.assertEqual(self.request("GET","/api/product-assets/photo-tee").json["print_surfaces"],{"front_center":"PRINT_FRONT","back_center":"PRINT_BACK"})
        cleared=self.request("POST","/admin/products/photo-tee",data={
            "name":"Photo Tee","description":"A photo view test shirt.","price":"500","category_id":"1",
            "sizes":"M=0","colors":"white, navy","inventory":"10","printing_charge":"99","design_charge":"0",
            "discount_percent":"0","enabled":"on","featured":"on","replace_photo_views":["front"],
            "print_surfaces":"{\"front_center\":\"PRINT_FRONT\",\"back_center\":\"PRINT_BACK\"}",
        })
        self.assertEqual(cleared.status_code,302)
        with closing(sqlite3.connect(website.DATABASE)) as db:
            updated_row=db.execute("SELECT photo_views_json,photo_print_areas_json FROM products WHERE slug='photo-tee'").fetchone()
            updated=json.loads(updated_row[0]);updated_areas=json.loads(updated_row[1])
        self.assertEqual(updated,{"back":views["back"]})
        self.assertEqual(updated_areas,photo_areas)
        self.request("POST","/admin/products/photo-tee",data={"action":"delete"})

    def test_photo_print_boundary_settings_reject_invalid_geometry(self):
        with self.assertRaisesRegex(ValueError,"convex quadrilateral"):
            website.parse_photo_print_areas('{"front":{"front_center":[[0,0],[1,1],[0,1],[1,0]]}}',"tshirt")
        with self.assertRaisesRegex(ValueError,"between 0 and 1"):
            website.parse_photo_print_areas('{"front":{"front_center":[[-0.1,0],[1,0],[1,1],[0,1]]}}',"tshirt")
        with self.assertRaisesRegex(ValueError,"supported print location"):
            website.parse_photo_print_areas('{"front":{"unknown":[[0,0],[1,0],[1,1],[0,1]]}}',"tshirt")


    def test_admin_product_database_failure_is_visible_and_rolls_back(self):
        self.create_admin()
        self.request("POST","/admin/login",data={"email":"shop@example.com","password":"separate-admin-secret-123"})
        with closing(sqlite3.connect(website.DATABASE)) as db:
            db.execute("CREATE TRIGGER fail_new_product BEFORE INSERT ON products BEGIN SELECT RAISE(ABORT, 'forced test failure'); END")
            db.commit()
        response=self.request("POST","/admin/products",data={
            "name":"Unavailable Product","slug":"unavailable-product","description":"A valid product for failure testing.",
            "price":"250","category_id":"1","sizes":"Standard=0","colors":"white","inventory":"4",
        },follow_redirects=True)
        self.assertEqual(response.status_code,200)
        self.assertIn(b"database rejected this product",response.data)
        self.assertNotIn(b"forced test failure",response.data)
        with closing(sqlite3.connect(website.DATABASE)) as db:
            self.assertIsNone(db.execute("SELECT 1 FROM products WHERE slug='unavailable-product'").fetchone())

    def invoke_admin_password_reset(self, email, passwords):
        with patch("builtins.input", return_value=email), patch("getpass.getpass", side_effect=passwords):
            return website.app.test_cli_runner().invoke(args=["reset-admin-password"])

    def _post_with_csrf(self, client, path, data):
        values=dict(data)
        with client.session_transaction() as session:
            token=session.setdefault("_csrf_token",secrets.token_urlsafe(32))
        values["csrf_token"]=token
        return client.post(path,data=values)

    def test_order_delivery_lifecycle_customer_notification_and_completed_queue(self):
        self.request("POST","/register",data={
            "name":"Delivery Customer","email":"delivery@example.com","phone":"5551112222","password":"delivery-customer-password-123",
        })
        self.request("POST","/login",data={"email":"delivery@example.com","password":"delivery-customer-password-123"})
        add=self.request("POST","/cart/add",headers={"X-Requested-With":"XMLHttpRequest"},data={
            "product":"mug","size":"11 oz","quantity":"1","design_images":[(self.png("green"),"delivery.png")],
        },content_type="multipart/form-data")
        self.assertEqual(add.status_code,201,add.get_data(as_text=True))
        checkout=self.request("POST","/checkout",data={
            "customer_name":"Ignored","email":"ignored@example.com","phone":"000","address":"12 Print Lane",
        })
        self.assertEqual(checkout.status_code,200)
        with closing(sqlite3.connect(website.DATABASE)) as db:
            order_id,customer_id,status=db.execute("SELECT id,customer_id,status FROM orders ORDER BY id DESC LIMIT 1").fetchone()
            self.assertIsNotNone(customer_id)
            self.assertEqual(status,"Pending")
        self.create_admin()
        admin=website.app.test_client()
        login=self._post_with_csrf(admin,"/admin/login",{"email":"shop@example.com","password":"separate-admin-secret-123"})
        self.assertEqual(login.status_code,302)
        self.assertEqual(admin.get("/admin/completed-orders").status_code,200)

        anon=website.app.test_client()
        blocked=self._post_with_csrf(anon,f"/admin/orders/{order_id}",{"status":"Delivered"})
        self.assertEqual(blocked.status_code,302)
        self.assertIn("/admin/login",blocked.headers["Location"])
        with closing(sqlite3.connect(website.DATABASE)) as db:
            self.assertEqual(db.execute("SELECT status FROM orders WHERE id=?",(order_id,)).fetchone()[0],"Pending")

        for status in ("Processing","Shipped","Delivered"):
            updated=self._post_with_csrf(admin,f"/admin/orders/{order_id}",{"status":status})
            self.assertEqual(updated.status_code,302)
        with closing(sqlite3.connect(website.DATABASE)) as db:
            status,delivered_at=db.execute("SELECT status,delivered_at FROM orders WHERE id=?",(order_id,)).fetchone()
            notices=db.execute("SELECT customer_id,order_id,message,created_at FROM customer_notifications").fetchall()
        self.assertEqual(status,"Delivered")
        self.assertIsNotNone(delivered_at)
        self.assertEqual(len(notices),1)
        self.assertEqual(notices[0][0],customer_id)
        self.assertEqual(notices[0][1],order_id)
        self.assertIn(f"order #{order_id}",notices[0][2])
        self.assertIn("successfully delivered",notices[0][2])
        self.assertEqual(notices[0][3],delivered_at)

        completed=admin.get("/admin/completed-orders")
        self.assertEqual(completed.status_code,200)
        self.assertIn(f"#{order_id}".encode(),completed.data)
        self.assertIn(b"Delivery Customer",completed.data)
        self.assertIn(b"Coffee Mugs / Cups",completed.data)
        self.assertIn(delivered_at.encode(),completed.data)
        detail=admin.get(f"/admin/orders/{order_id}")
        self.assertEqual(detail.status_code,200)
        self.assertIn(b"Delivered",detail.data)
        self.assertIn(delivered_at.encode(),detail.data)

        repeated=self._post_with_csrf(admin,f"/admin/orders/{order_id}",{"status":"Delivered"})
        self.assertEqual(repeated.status_code,302)
        repeated_detail=admin.get(f"/admin/orders/{order_id}")
        self.assertIn(b"already completed",repeated_detail.data)
        with closing(sqlite3.connect(website.DATABASE)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM customer_notifications WHERE order_id=?",(order_id,)).fetchone()[0],1)
            self.assertEqual(db.execute("SELECT delivered_at FROM orders WHERE id=?",(order_id,)).fetchone()[0],delivered_at)

        own_orders=self.request("GET","/account/orders")
        self.assertIn(b"Order Completed Successfully",self.request("GET",f"/account/orders/{order_id}").data)
        self.assertIn(b"successfully delivered",own_orders.data)
        self.assertIn(b"Delivered",own_orders.data)
        self.assertIn(b"Notifications",self.request("GET","/account/profile").data)
        with closing(sqlite3.connect(website.DATABASE)) as db:
            self.assertEqual(db.execute("PRAGMA table_info(orders)").fetchall()[-1][1],"delivered_at")

        other=website.app.test_client()
        self._post_with_csrf(other,"/register",{
            "name":"Other Customer","email":"other-delivery@example.com","phone":"5553334444","password":"other-customer-password-123",
        })
        self._post_with_csrf(other,"/login",{"email":"other-delivery@example.com","password":"other-customer-password-123"})
        self.assertEqual(other.get(f"/account/orders/{order_id}").status_code,404)
        self.assertNotIn(b"successfully delivered",other.get("/account/orders").data)
        self.assertNotIn(b"successfully delivered",other.get("/account/profile").data)
        customer_status_attempt=self._post_with_csrf(other,f"/admin/orders/{order_id}",{"status":"Processing"})
        self.assertEqual(customer_status_attempt.status_code,302)
        self.assertIn("/admin/login",customer_status_attempt.headers["Location"])
        with closing(sqlite3.connect(website.DATABASE)) as db:
            self.assertEqual(db.execute("SELECT status FROM orders WHERE id=?",(order_id,)).fetchone()[0],"Delivered")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM customer_notifications WHERE order_id=?",(order_id,)).fetchone()[0],1)

    def test_admin_password_reset_updates_existing_account_only(self):
        self.create_admin()
        other_password="Other-Admin-Password-123"
        with closing(sqlite3.connect(website.DATABASE)) as db:
            db.execute("INSERT INTO admin_users(email,password_hash,created_at) VALUES(?,?,?)",(
                "other-shop@example.com",website.generate_password_hash(other_password),"2026-01-02T00:00:00"))
            db.commit()
            original_admins=dict(db.execute("SELECT email,password_hash FROM admin_users").fetchall())
            original_counts={table:db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                             for table in ("customers","products","orders")}
        new_password="New-Secure-Password-2026"
        result=self.invoke_admin_password_reset(" SHOP@example.com ",[new_password,new_password])
        self.assertEqual(result.exit_code,0,result.output)
        self.assertIn("Admin password updated successfully.",result.output)
        self.assertNotIn(new_password,result.output)
        with closing(sqlite3.connect(website.DATABASE)) as db:
            admins=dict(db.execute("SELECT email,password_hash FROM admin_users").fetchall())
            counts={table:db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in ("customers","products","orders")}
        self.assertEqual(len(admins),len(original_admins))
        self.assertTrue(website.check_password_hash(admins["shop@example.com"],new_password))
        self.assertFalse(website.check_password_hash(admins["shop@example.com"],"separate-admin-secret-123"))
        self.assertEqual(admins["other-shop@example.com"],original_admins["other-shop@example.com"])
        self.assertEqual(counts,original_counts)

    def test_homepage_promo_banner_crud_never_changes_customize_section(self):
        self.create_admin()
        self.request("POST", "/admin/login", data={"email":"shop@example.com","password":"separate-admin-secret-123"})
        with patch.object(website, "BASE_DIR", self.root):
            hero_update=self.request("POST", "/admin/website", data={
                "hero_title":"Print your ideas", "hero_subtitle":"Create a custom design.",
                "promo_text":"Made for you", "banner_image":(self.png("yellow"),"hero.png"),
            }, content_type="multipart/form-data")
            self.assertEqual(hero_update.status_code,302)
            home=self.request("GET","/")
            self.assertIn(b"Upload. Preview. Print.",home.data)
            self.assertIn(b'class="home-customize customize-trigger"',home.data)
            initial_hero_banner=website.settings_map()["banner_image"]
            original_products=website.connect_db().execute("SELECT slug,image FROM products ORDER BY slug").fetchall()

            add=self.request("POST", "/admin/website", data={
                "action":"banner_add", "banner_title":"Spring printing sale", "banner_url":"/products",
                "promo_banner_image":(self.png("red"),"spring.png"),
            }, content_type="multipart/form-data")
            self.assertEqual(add.status_code,302)
            home=self.request("GET","/")
            self.assertIn(b"Spring printing sale",home.data)
            self.assertIn(b'id="about"',home.data)
            self.assertIn(b"Upload. Preview. Print.",home.data)
            self.assertIn(b'class="home-customize customize-trigger"',home.data)
            banners=website.homepage_banners_from_settings(website.settings_map())
            self.assertEqual(len(banners),1)
            banner_id=banners[0]["id"]

            update=self.request("POST", "/admin/website", data={
                "action":"banner_update", "banner_id":banner_id,
                "banner_title":"Updated spring sale", "banner_url":"/products?sort=featured",
                "promo_banner_image":(self.png("blue"),"spring-updated.png"),
            }, content_type="multipart/form-data")
            self.assertEqual(update.status_code,302)
            home=self.request("GET","/")
            self.assertIn(b"Updated spring sale",home.data)
            self.assertIn(b"Upload. Preview. Print.",home.data)
            banners=website.homepage_banners_from_settings(website.settings_map())
            self.assertEqual(len(banners),1)
            updated_image=banners[0]["image"]
            self.assertNotEqual(updated_image, "")

            second=self.request("POST", "/admin/website", data={
                "action":"banner_add", "banner_title":"Weekend offer", "banner_url":"https://example.com/offer",
                "promo_banner_image":(self.png("green"),"weekend.png"),
            }, content_type="multipart/form-data")
            self.assertEqual(second.status_code,302)
            banners=website.homepage_banners_from_settings(website.settings_map())
            self.assertEqual(len(banners),2)
            first_id=next(item["id"] for item in banners if item["title"]=="Updated spring sale")
            first_image=next(item["image"] for item in banners if item["id"]==first_id)
            update_one=self.request("POST", "/admin/website", data={
                "action":"banner_update", "banner_id":first_id,
                "banner_title":"Spring sale refreshed", "banner_url":"/products",
            })
            self.assertEqual(update_one.status_code,302)
            banners=website.homepage_banners_from_settings(website.settings_map())
            self.assertEqual(len(banners),2)
            self.assertEqual(next(item["image"] for item in banners if item["id"]==first_id),first_image)
            self.assertIn("Weekend offer",[item["title"] for item in banners])

            delete=self.request("POST", "/admin/website", data={"action":"banner_delete","banner_id":first_id})
            self.assertEqual(delete.status_code,302)
            home=self.request("GET","/")
            self.assertNotIn(b"Spring sale refreshed",home.data)
            self.assertIn(b"Weekend offer",home.data)
            self.assertIn(b'id="about"',home.data)
            self.assertIn(b"Upload. Preview. Print.",home.data)
            self.assertIn(b'class="home-customize customize-trigger"',home.data)
            self.assertIn(b'href="/products" class="primary-btn">Start Designing',home.data)

            current_settings=website.settings_map()
            self.assertEqual(current_settings["banner_image"],initial_hero_banner)
            self.assertEqual(website.connect_db().execute("SELECT slug,image FROM products ORDER BY slug").fetchall(),original_products)
            self.assertTrue((self.root / "static" / updated_image).is_file(), "Removing/updating a banner must retain its old file")

            final_id=website.homepage_banners_from_settings(current_settings)[0]["id"]
            self.request("POST", "/admin/website", data={"action":"banner_delete","banner_id":final_id})
            home=self.request("GET","/")
            self.assertNotIn(b"homepage-promotions",home.data)
            self.assertIn(b'id="about"',home.data)
            self.assertIn(b"Upload. Preview. Print.",home.data)
            self.assertIn(b'class="home-customize customize-trigger"',home.data)

    def test_homepage_promo_banner_invalid_add_preserves_settings(self):
        self.create_admin()
        self.request("POST", "/admin/login", data={"email":"shop@example.com","password":"separate-admin-secret-123"})
        with patch.object(website, "BASE_DIR", self.root):
            before=website.settings_map()
            invalid=self.request("POST", "/admin/website", data={
                "action":"banner_add", "banner_title":"Invalid link", "banner_url":"//evil.example",
                "promo_banner_image":(self.png("red"),"promo.png"),
            }, content_type="multipart/form-data", follow_redirects=True)
            self.assertEqual(invalid.status_code,200)
            self.assertIn(b"Banner link must be a site path",invalid.data)
            self.assertEqual(website.settings_map(),before)
            self.assertIn(b'id="about"',self.request("GET","/").data)

    def test_admin_password_reset_unknown_email_changes_nothing(self):
        self.create_admin()
        with closing(sqlite3.connect(website.DATABASE)) as db:
            before=db.execute("SELECT password_hash FROM admin_users WHERE email='shop@example.com'").fetchone()[0]
        password="Unknown-Email-Password-2026"
        result=self.invoke_admin_password_reset("missing@example.com",[password,password])
        self.assertNotEqual(result.exit_code,0)
        self.assertIn("No admin account exists for that email",result.output)
        self.assertNotIn(password,result.output)
        with closing(sqlite3.connect(website.DATABASE)) as db:
            after=db.execute("SELECT password_hash FROM admin_users WHERE email='shop@example.com'").fetchone()[0]
        self.assertEqual(after,before)

    def test_admin_password_reset_rejects_incorrect_confirmation(self):
        self.create_admin()
        with closing(sqlite3.connect(website.DATABASE)) as db:
            before=db.execute("SELECT password_hash FROM admin_users WHERE email='shop@example.com'").fetchone()[0]
        password="Correct-Password-2026"
        result=self.invoke_admin_password_reset("shop@example.com",[password,"Different-Password-2026"])
        self.assertNotEqual(result.exit_code,0)
        self.assertIn("Password confirmation does not match",result.output)
        self.assertNotIn(password,result.output)
        with closing(sqlite3.connect(website.DATABASE)) as db:
            after=db.execute("SELECT password_hash FROM admin_users WHERE email='shop@example.com'").fetchone()[0]
        self.assertEqual(after,before)

    def test_admin_auth_product_price_order_controls_and_storefront_price(self):
        self.assertEqual(self.request("GET","/admin").status_code,302)
        self.create_admin()
        customer_credentials=self.request("POST","/admin/login",data={"email":"shop@example.com","password":"customer-only-password-123"})
        self.assertEqual(customer_credentials.status_code,401)
        signed=self.request("POST","/admin/login",data={"email":"shop@example.com","password":"separate-admin-secret-123"})
        self.assertEqual(signed.status_code,302)
        self.assertEqual(signed.headers["Location"],"/admin")
        dashboard=self.request("GET","/admin")
        self.assertEqual(dashboard.status_code,200)
        self.assertIn("Dashboard Overview".encode(),dashboard.data)
        self.assertNotIn(b">?</i>",dashboard.data)
        self.assertEqual(self.request("GET","/admin/products").status_code,200)
        self.assertEqual(self.request("GET","/admin/categories").status_code,200)
        for path in ("/admin/prices","/admin/inventory","/admin/orders","/admin/completed-orders","/admin/customers","/admin/website","/admin/offers","/admin/reports","/admin/settings"):
            with self.subTest(admin_path=path):self.assertEqual(self.request("GET",path).status_code,200)

        prices=self.request("POST","/admin/prices",data={"printing_charge":"150","design_charge":"20","delivery_charge":"25"})
        self.assertEqual(prices.status_code,302)
        updated=self.request("POST","/admin/products/tshirt",data={
            "name":"Premium Custom Tees","description":"Updated shirt description","price":"799","category_id":"1",
            "sizes":"S=0\nM=0\nL=50","colors":"white, navy","inventory":"23","printing_charge":"150","design_charge":"20","discount_percent":"0","enabled":"on","featured":"on",
        })
        self.assertEqual(updated.status_code,302)
        public=self.request("GET","/products")
        self.assertIn(b"Premium Custom Tees",public.data)
        self.assertIn(b"799",public.data)
        add=self.request("POST","/cart/add",headers={"X-Requested-With":"XMLHttpRequest"},data={
            "product":"tshirt","size":"M","quantity":"1","design_images":[(self.png("red"),"price.png")],
        },content_type="multipart/form-data")
        self.assertEqual(add.status_code,201,add.get_data(as_text=True))
        self.assertEqual(add.get_json()["cart_total"],969)
        offer=self.request("POST","/admin/offers",data={"slug":"tshirt","discount_percent":"10"})
        self.assertEqual(offer.status_code,302)
        self.assertEqual(self.request("GET","/cart").status_code,200)
        checkout=self.request("POST","/checkout",data={"customer_name":"Guest","email":"guest@example.com","phone":"123","address":"Guest home"})
        self.assertEqual(checkout.status_code,200)
        with closing(sqlite3.connect(website.DATABASE)) as db:
            order_id=db.execute("SELECT id FROM orders ORDER BY id DESC LIMIT 1").fetchone()[0]
        status=self.request("POST",f"/admin/orders/{order_id}",data={"status":"Processing"})
        self.assertEqual(status.status_code,302)
        with closing(sqlite3.connect(website.DATABASE)) as db:
            self.assertEqual(db.execute("SELECT status FROM orders WHERE id=?",(order_id,)).fetchone()[0],"Processing")
        self.assertEqual(self.request("GET","/admin/orders?q=guest&status=Processing").status_code,200)
        detail=self.request("GET",f"/admin/orders/{order_id}");self.assertEqual(detail.status_code,200);self.assertIn(b"Uploaded print artwork",detail.data)
        self.assertEqual(self.request("GET","/admin/reports").status_code,200)
        with closing(sqlite3.connect(website.DATABASE)) as db:db.execute("UPDATE products SET inventory=0 WHERE slug='mug'");db.commit()
        website.initialize_database()
        with closing(sqlite3.connect(website.DATABASE)) as db:self.assertEqual(db.execute("SELECT inventory FROM products WHERE slug='mug'").fetchone()[0],0)

    def test_catalog_studios_and_local_banner_fallback(self):
        home = self.request("GET", "/")
        catalog_page = self.request("GET", "/products")
        self.assertEqual(home.status_code, 200)
        self.assertEqual(catalog_page.status_code, 200)
        for page in (home,catalog_page):
            self.assertEqual(page.data.count(b'href="/admin/login"'),2)
            self.assertEqual(page.data.count(b"Admin Login"),2)
        favicon=self.request("GET","/favicon.ico")
        self.assertEqual(favicon.status_code,200)
        self.assertEqual(favicon.mimetype,"image/svg+xml")
        self.assertIn(b"&#8377;", home.data)
        self.assertIn(b"Customize Now &rarr;", home.data)
        self.assertNotIn(b"strong>?", home.data)
        self.assertEqual(home.data.count(b'id="designModal"'), 1)
        self.assertEqual(catalog_page.data.count(b'id="designModal"'), 1)
        self.assertEqual(home.data.count(b'class="home-customize customize-trigger"'), 4)
        self.assertEqual(catalog_page.data.count(b'class="catalog-link customize-trigger"'), 8)
        self.assertEqual(home.data.count(b'class="ready-design-launch home-ready-design"'), 4)
        self.assertEqual(catalog_page.data.count(b'class="ready-design-launch"'), 8)
        self.assertNotIn(b'href="/design?product=', home.data)
        self.assertNotIn(b'href="/design?product=', catalog_page.data)
        self.assertIn(b"/static/images/storefront-banner-reference.png", home.data)
        self.assertIn(b"/static/images/storefront-banner-reference.png", catalog_page.data)
        self.assertIn(b"/static/images/storefront-banner-reference.png", catalog_page.data)
        banner_image = self.request("GET", "/static/images/storefront-banner-reference.png")
        self.assertEqual(banner_image.status_code, 200)
        self.assertEqual(banner_image.mimetype, "image/png")
        self.assertNotIn(b"loopnet", home.data.lower() + catalog_page.data.lower())
        self.assertIn(b"design-3d-loader.js", catalog_page.data)
        self.assertIn(b"design.css", catalog_page.data)
        for document in (home, catalog_page):
            audit = MarkupAudit(); audit.feed(document.get_data(as_text=True)); audit.close()
            self.assertFalse(audit.errors, audit.errors)
            self.assertFalse(audit.stack, audit.stack)
            self.assertEqual(len(audit.ids), len(set(audit.ids)), "duplicate HTML id")
        self.assertEqual(catalog_page.data.count(b"_design_editor.html"), 0)
        for slug in ("tshirt", "mug", "pillow", "banner", "frame", "phonecase", "poster", "gift"):
            with self.subTest(product=slug):
                self.assertIn(f'data-customize="{slug}"'.encode(), catalog_page.data)
                response = self.request("GET", f"/design?product={slug}")
                self.assertEqual(response.status_code, 200)
                self.assertIn(b"productRotate360", response.data)
                self.assertIn(b"Add Multiple Photos", response.data)
                if slug == "tshirt":
                    self.assertIn(b"Front View", response.data)
                    self.assertIn(b"Back View", response.data)
                    self.assertIn(b"Side View", response.data)
                    self.assertIn(b"textFontSelect", response.data)
                    self.assertIn(b"textItalicInput", response.data)
        fallback = self.request("GET", "/static/storefront-banner.svg")
        self.assertEqual(fallback.status_code, 200)
        self.assertIn(b"image/svg+xml", fallback.content_type.encode())
        for asset in ("style.css", "design.css", "design.js", "design-3d-loader.js", "design-3d.js"):
            with self.subTest(asset=asset):
                self.assertEqual(self.request("GET", f"/static/{asset}").status_code, 200)

    def test_multi_photo_settings_cart_and_order_persist(self):
        settings = [
            {"scale": 1.4, "rotation": 23, "offset_x": 14, "offset_y": -6, "location": "full_front"},
            {"scale": .8, "rotation": -15, "offset_x": -10, "offset_y": 12, "location": "full_front"},
        ]
        response = self.request("POST", "/cart/add", data={
            "product": "banner", "size": "2 × 3 ft", "quantity": "2", "color": "white",
            "artwork_settings": json.dumps(settings),
            "design_images": [(self.png("red"), "first.png"), (self.png("blue"), "second.png")],
        }, content_type="multipart/form-data", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as session:
            item = session["cart"][0]
            self.assertEqual(len(item["artworks"]), 2)
            self.assertEqual(item["artworks"][0]["rotation"], 23)
            self.assertEqual(item["artworks"][1]["offset_y"], 12)
            self.assertEqual(item["artworks"][0]["location"], "full_front")
            self.assertEqual(item["quantity"], 2)
            self.assertEqual(item["unit_price"], 1398)
        self.assertIn(b"2 photo(s)", response.data)

        for artwork in item["artworks"]:
            with self.request("GET", f"/uploads/{artwork['image']}") as uploaded_response:
                self.assertEqual(uploaded_response.status_code, 200)
                self.assertTrue(uploaded_response.data)
        checkout = self.request("POST", "/checkout", data={
            "customer_name": "Studio Test", "email": "test@example.com",
            "phone": "1234567890", "address": "Test address",
        })
        self.assertEqual(checkout.status_code, 200)
        self.assertIn(b"ORDER RECEIVED", checkout.data)
        with closing(sqlite3.connect(website.DATABASE)) as db:
            saved = db.execute("SELECT items_json,total FROM orders").fetchone()
        saved_items = json.loads(saved[0])
        self.assertEqual(len(saved_items[0]["artworks"]), 2)
        self.assertEqual(saved_items[0]["artworks"][0]["rotation"], 23)
        self.assertEqual(saved[1], 2796)

    def test_single_upload_backward_compatibility_and_bad_image_rejection(self):
        accepted = self.request("POST", "/cart/add", data={
            "product": "mug", "size": "11 oz", "quantity": "1",
            "design_image": (self.png("green"), "single.png"),
        }, content_type="multipart/form-data", follow_redirects=True)
        self.assertEqual(accepted.status_code, 200)
        with self.client.session_transaction() as session:
            self.assertEqual(len(session["cart"][0]["artworks"]), 1)
        rejected = self.request("POST", "/cart/add", data={
            "product": "mug", "design_images": [(io.BytesIO(b"not an image"), "bad.png")],
        }, content_type="multipart/form-data", follow_redirects=True)
        self.assertEqual(rejected.status_code, 200)
        self.assertIn(b"images are invalid", rejected.data.lower())

    def test_ready_made_webp_is_saved_unchanged_and_ordered_with_mode(self):
        source = self.webp("orange", size=(5000, 4500))
        original = source.getvalue()
        response = self.request("POST", "/cart/add", headers={
            "X-Requested-With": "XMLHttpRequest", "Accept": "application/json",
        }, data={
            "product": "poster", "design_mode": "ready", "size": "A3", "quantity": "2", "color": "white",
            "design_images": [(source, "finished-art.webp")],
        }, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        payload = response.get_json()
        self.assertEqual(payload["items"][0]["design_mode"], "ready")
        with self.client.session_transaction() as session:
            item = session["cart"][0]
            artwork = item["artworks"][0]
            self.assertEqual(item["design_mode"], "ready")
            stored_name = artwork["image"]
            self.assertTrue(stored_name.endswith(".webp"))
            self.assertNotIn("finished-art", stored_name)
        stored = self.root / "uploads" / stored_name
        self.assertEqual(stored.read_bytes(), original)
        cart_page = self.request("GET", "/cart")
        self.assertIn(b"READY-MADE FILE", cart_page.data)
        checkout = self.request("POST", "/checkout", data={
            "customer_name": "Ready Artwork", "email": "ready@example.com",
            "phone": "1112223333", "address": "Ready test address",
        })
        self.assertEqual(checkout.status_code, 200)
        with closing(sqlite3.connect(website.DATABASE)) as db:
            saved = db.execute("SELECT items_json FROM orders").fetchone()
        ordered = json.loads(saved[0])[0]
        self.assertEqual(ordered["design_mode"], "ready")
        self.assertEqual(ordered["artworks"][0]["image"], stored_name)
        self.assertEqual((self.root / "uploads" / ordered["artworks"][0]["image"]).read_bytes(), original)

    def test_ready_made_mode_rejects_mismatched_extension_and_multiple_files(self):
        mismatch = self.request("POST", "/cart/add", data={
            "product": "mug", "design_mode": "ready",
            "design_images": [(self.webp("blue"), "finished.png")],
        }, content_type="multipart/form-data", follow_redirects=True)
        self.assertEqual(mismatch.status_code, 200)
        self.assertIn(b"does not contain a valid PNG", mismatch.data)
        multiple = self.request("POST", "/cart/add", data={
            "product": "mug", "design_mode": "ready",
            "design_images": [(self.png("red"), "one.png"), (self.png("blue"), "two.png")],
        }, content_type="multipart/form-data", follow_redirects=True)
        self.assertEqual(multiple.status_code, 200)
        self.assertIn(b"one completed design", multiple.data)

    def test_ready_made_size_and_resolution_limits_are_enforced_and_reported(self):
        self.assertEqual(website.MAX_READY_UPLOAD_BYTES, 80 * 1024 * 1024)
        self.assertEqual(website.MAX_IMAGE_PIXELS, 120_000_000)
        self.assertGreaterEqual(website.app.config["MAX_CONTENT_LENGTH"], website.MAX_READY_UPLOAD_BYTES)
        script = (website.BASE_DIR / "static" / "design.js").read_text(encoding="utf-8")
        editor = (website.BASE_DIR / "templates" / "_design_editor.html").read_text(encoding="utf-8")
        self.assertIn("80 MB limit", script)
        self.assertIn("120 megapixel resolution limit", script)
        self.assertIn("up to 80 MB and 120 megapixels", editor)

        with patch.object(website, "MAX_READY_UPLOAD_BYTES", 1024 * 1024):
            oversized = self.request("POST", "/cart/add", data={
                "product": "mug", "design_mode": "ready",
                "design_images": [(io.BytesIO(b"x" * (1024 * 1024 + 1)), "large.webp")],
            }, content_type="multipart/form-data", follow_redirects=True)
        self.assertEqual(oversized.status_code, 200)
        self.assertIn(b"Each image must be 1 MB or smaller", oversized.data)

        with patch.object(website, "MAX_IMAGE_PIXELS", 3_000_000):
            too_many_pixels = self.request("POST", "/cart/add", data={
                "product": "mug", "design_mode": "ready",
                "design_images": [(self.webp("teal", size=(2000, 2000)), "huge.webp")],
            }, content_type="multipart/form-data", follow_redirects=True)
        self.assertEqual(too_many_pixels.status_code, 200)
        self.assertIn(b"no more than 3 megapixels", too_many_pixels.data)

    def test_ajax_add_to_cart_keeps_customer_on_same_page_and_checkout_saves(self):
        response = self.request("POST", "/cart/add", headers={
            "X-Requested-With": "XMLHttpRequest", "Accept": "application/json",
        }, data={
            "product": "phonecase", "size": "iPhone", "quantity": "1",
            "artwork_settings": json.dumps([{"scale": 1.2, "rotation": 18, "offset_x": 8, "offset_y": -4}]),
            "design_images": [(self.png("purple"), "phone.png")],
        }, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["cart_count"], 1)
        self.assertEqual(payload["items"][0]["artwork_count"], 1)
        self.assertEqual(payload["items"][0]["name"], "Phone Covers")
        artwork = payload["items"][0]["artworks"][0]
        self.assertRegex(artwork["image_url"], r"^/uploads/[a-f0-9]+\.png$")
        self.assertEqual(artwork["filename"], artwork["image_url"].rsplit("/", 1)[1])
        self.assertEqual(artwork["content_type"], "image/png")
        self.assertEqual((artwork["width"], artwork["height"]), (120, 100))
        self.assertEqual(artwork["size"], len(self.png("purple").getvalue()))
        with self.request("GET", artwork["image_url"]) as image_response:
            self.assertEqual(image_response.status_code, 200)
            self.assertEqual(image_response.mimetype, "image/png")
            self.assertTrue(image_response.data)

        checkout = self.request("POST", "/checkout", data={
            "customer_name": "One Page Test", "email": "onepage@example.com",
            "phone": "9876543210", "address": "One-page test address",
        })
        self.assertEqual(checkout.status_code, 200)
        self.assertIn(b"ORDER RECEIVED", checkout.data)
        with closing(sqlite3.connect(website.DATABASE)) as db:
            row = db.execute("SELECT items_json FROM orders").fetchone()
        ordered_items = json.loads(row[0])
        self.assertEqual(ordered_items[0]["slug"], "phonecase")
        self.assertEqual(ordered_items[0]["artworks"][0]["rotation"], 18)

    def test_upload_response_preserves_transparent_png_and_returns_openable_url(self):
        source = io.BytesIO()
        image = Image.new("RGBA", (120, 100), (0, 0, 0, 0))
        image.putpixel((60, 50), (220, 30, 80, 180))
        image.save(source, "PNG"); image.close(); source.seek(0)
        original = source.getvalue()
        response = self.request("POST", "/cart/add", headers={
            "X-Requested-With": "XMLHttpRequest", "Accept": "application/json",
        }, data={"product": "tshirt", "design_images": [(source, "transparent.png")]}, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        artwork = response.get_json()["items"][0]["artworks"][0]
        self.assertEqual(artwork["size"], len(original))
        self.assertEqual((artwork["width"], artwork["height"]), (120, 100))
        with self.request("GET", artwork["image_url"]) as saved_response:
            self.assertEqual(saved_response.status_code, 200)
            self.assertEqual(saved_response.data, original)
        with Image.open(self.root / "uploads" / artwork["filename"]) as stored:
            self.assertEqual(stored.mode, "RGBA")
            self.assertEqual(stored.getpixel((60, 50)), (220, 30, 80, 180))

    def test_upload_json_errors_for_invalid_or_missing_files(self):
        headers = {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"}
        invalid = self.request("POST", "/cart/add", headers=headers, data={
            "product": "mug", "design_images": [(io.BytesIO(b"not an image"), "bad.png")],
        }, content_type="multipart/form-data")
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.get_json()["success"], False)
        self.assertIn("invalid", invalid.get_json()["message"].lower())
        missing = self.request("POST", "/cart/add", headers=headers, data={"product": "mug"}, content_type="multipart/form-data")
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(missing.get_json()["success"], False)
        self.assertIn("upload", missing.get_json()["message"].lower())
        with patch("werkzeug.datastructures.FileStorage.save", side_effect=OSError("disk full")):
            failed_save = self.request("POST", "/cart/add", headers=headers, data={
                "product": "mug", "design_images": [(self.png("blue"), "disk-full.png")],
            }, content_type="multipart/form-data")
        self.assertEqual(failed_save.status_code, 500)
        self.assertFalse(failed_save.get_json()["success"])
        self.assertIn("could not be saved", failed_save.get_json()["message"].lower())
        with self.client.session_transaction() as session:
            self.assertEqual(session.get("cart", []), [])

    def test_customer_design_order_admin_print_exports_and_private_downloads(self):
        self.assertEqual(website.print_spec({"slug":"new-custom-product"},"front"),(3600,3600,300))
        self.request("POST", "/register", data={
            "name":"Print Customer", "email":"print-customer@example.com",
            "phone":"1234567890", "password":"customer-test-secret-123",
        })
        self.request("POST", "/login", data={"email":"print-customer@example.com","password":"customer-test-secret-123"})
        headers={"X-Requested-With":"XMLHttpRequest","Accept":"application/json"}
        settings=[
            {"layer_id":"photo-front","layer_order":0,"type":"image","location":"front_center","view":"front",
             "scale":1.4,"rotation":27,"position_x":.12,"position_y":-.08,"width_ratio":1.148,"height_ratio":1.148},
            {"layer_id":"text-back","layer_order":1,"type":"text","location":"back_center","view":"back",
             "scale":.7,"rotation":-12,"position_x":-.1,"position_y":.16,"text_spec":{
                 "text":"Print test","font":"Impact","size":92,"color":"red","bold":True,"italic":False}},
            {"layer_id":"side-logo","layer_order":2,"type":"image","location":"front_center","view":"side",
             "scale":.6,"rotation":0,"position_x":.15,"position_y":0},
        ]
        snapshot={"schema_version":1,"product":"tshirt","variant":"M","color":"white","mode":"create",
                  "view":"back","product_angle":180,"product_zoom":.9,"default_location":"back_center",
                  "print_areas":["front_center","back_center"],"layers":settings}
        first=self.png("purple"); first_bytes=first.getvalue()
        second=self.png("orange"); second_bytes=second.getvalue()
        third=self.png("green"); third_bytes=third.getvalue()
        added=self.request("POST","/cart/add",headers=headers,data={
            "product":"tshirt","size":"M","color":"white","quantity":"1",
            "artwork_settings":json.dumps(settings),"design_snapshot":json.dumps(snapshot),
            "design_images":[(first,"customer-original.png"),(second,"customer-text-render.png"),(third,"side-logo.png")],
        },content_type="multipart/form-data")
        self.assertEqual(added.status_code,201,added.get_data(as_text=True))
        with self.client.session_transaction() as session:
            cart_item=session["cart"][0]
            self.assertEqual(len(cart_item["artworks"]),3)
            self.assertEqual((cart_item["artworks"][0]["position_x"],cart_item["artworks"][0]["rotation"]),(.12,27))
            self.assertEqual(cart_item["artworks"][1]["text_spec"]["text"],"Print test")
            design_id=cart_item["design_id"]
            file_names=[layer["image"] for layer in cart_item["artworks"]]
        self.assertEqual((self.root/"uploads"/file_names[0]).read_bytes(),first_bytes)
        self.assertEqual((self.root/"uploads"/file_names[1]).read_bytes(),second_bytes)
        self.assertEqual((self.root/"uploads"/file_names[2]).read_bytes(),third_bytes)
        checkout=self.request("POST","/checkout",data={"customer_name":"ignored","email":"ignored@example.com",
            "phone":"000","address":"Customer print address"})
        self.assertEqual(checkout.status_code,200)
        with closing(sqlite3.connect(website.DATABASE)) as db:
            order_id,items_json=db.execute("SELECT id,items_json FROM orders ORDER BY id DESC LIMIT 1").fetchone()
        ordered=json.loads(items_json)[0]
        self.assertEqual(ordered["design"]["view"],"back")
        self.assertEqual(ordered["design"]["variant"],"M")
        self.assertEqual(ordered["design"]["order_id"],order_id)
        self.assertIsNotNone(ordered["design"]["customer_id"])
        self.assertEqual([art["location"] for art in ordered["artworks"]],["front_center","back_center","front_center"])
        self.assertTrue(all(art["order_id"]==order_id for art in ordered["artworks"]))
        self.assertEqual(ordered["artworks"][1]["text_spec"]["font"],"Impact")

        denied=self.request("GET",f"/admin/orders/{order_id}/print/{design_id}/front_center/front")
        self.assertEqual(denied.status_code,302)
        self.create_admin()
        self.request("POST","/admin/login",data={"email":"shop@example.com","password":"separate-admin-secret-123"})
        detail=self.request("GET",f"/admin/orders/{order_id}")
        self.assertEqual(detail.status_code,200)
        self.assertIn(b"Design &amp; Print Center",detail.data)
        self.assertIn(b"Print test",detail.data)
        self.assertIn(b"Download print-ready PNG",detail.data)
        self.assertIn(b"12.00",detail.data)
        self.assertIn(b"13.20",detail.data)
        for location,view,expected_file in zip(("front_center","back_center","front_center"),("front","back","side"),file_names):
            exported=self.request("GET",f"/admin/orders/{order_id}/print/{design_id}/{location}/{view}")
            self.assertEqual(exported.status_code,200)
            self.assertEqual(exported.mimetype,"image/png")
            with Image.open(io.BytesIO(exported.data)) as print_file:
                self.assertEqual(print_file.size,(3600,3960))
                self.assertAlmostEqual(print_file.info["dpi"][0],300,delta=1)
                self.assertEqual(print_file.mode,"RGBA")
                self.assertEqual(print_file.getpixel((0,0))[3],0)
                self.assertGreater(print_file.getbbox()[2],0)
                if view=="back":
                    center=print_file.crop((1300,1500,2300,2500))
                    self.assertTrue(any(color==(214,40,57,255) for _count,color in center.getcolors(maxcolors=100000) or []))
                    center.close()
            original=self.request("GET",f"/admin/orders/{order_id}/artwork/{design_id}/{expected_file}")
            self.assertEqual(original.status_code,200)
            self.assertEqual(original.data,(self.root/"uploads"/expected_file).read_bytes())
            self.assertIn("attachment",original.headers["Content-Disposition"])
            self.assertIn("private",original.headers["Cache-Control"])
            original.close()

        outsider=website.app.test_client()
        try:
            blocked=outsider.get(f"/admin/orders/{order_id}/artwork/{design_id}/{file_names[0]}")
            self.assertEqual(blocked.status_code,302)
        finally:
            outsider.__exit__(None,None,None)
        (self.root/"uploads"/file_names[0]).unlink()
        unavailable=self.request("GET",f"/admin/orders/{order_id}/print/{design_id}/front_center/front")
        self.assertEqual(unavailable.status_code,404)
        missing_detail=self.request("GET",f"/admin/orders/{order_id}")
        self.assertIn(b"original files for this print area are missing",missing_detail.data)

    def test_editor_switching_and_mobile_layout_are_wired(self):
        script = (website.BASE_DIR / "static" / "design.js").read_text(encoding="utf-8")
        styles = (website.BASE_DIR / "static" / "design.css").read_text(encoding="utf-8")
        self.assertIn("addedItems.forEach(addPreviewNode)", script)
        self.assertNotIn("accepted.forEach(addPreviewNode)", script)
        self.assertIn("productSelect.addEventListener('change', () => switchProduct(productSelect.value))", script)
        self.assertIn("document.querySelectorAll('[data-customize]')", script)
        self.assertIn("document.querySelectorAll('[data-ready-design]')", script)
        self.assertIn("setDesignMode('ready'); readyInput.click()", script)
        self.assertIn("fetch(form.action", script)
        self.assertIn("body.modal-open", styles)
        self.assertIn("@media(max-width:720px){.design-modal", styles)
        self.assertIn("flex-direction:column", styles)
        editor = (website.BASE_DIR / "templates" / "_design_editor.html").read_text(encoding="utf-8")
        self.assertIn("Create Your Own Design", editor)
        self.assertIn("Upload Ready-Made Design", editor)
        self.assertIn("image/webp", editor)
        self.assertIn("ready-design-mode", script)
        self.assertIn("data-assistant-prompt", editor)
        self.assertIn("assistantUploadImage", editor)
        self.assertIn("assistantClear", editor)
        self.assertIn("AI SETUP REQUIRED", editor)
        self.assertIn("resize_artwork", script)
        self.assertIn("move_artwork", script)
        self.assertIn("addTextArtwork", script)
        self.assertIn("setTextStyle", script)
        self.assertIn("undoDesign", script)
        self.assertIn("setTshirtView", script)
        rendered = self.request("GET", "/products").get_data(as_text=True)
        rendered_ids = set(re.findall(r'\bid="([^"]+)"', rendered))
        required_ids = set(re.findall(r"\$\('([^']+)'\)", script))
        self.assertFalse(required_ids - rendered_ids, f"Design script IDs missing from rendered markup: {sorted(required_ids - rendered_ids)}")
        self.assertIn("text_spec: item.textSpec || null", script)
        self.assertIn("#tshirtViewControls", styles)
        self.assertIn("@media(max-width:720px)", styles)
        self.assertIn("@media(min-width:1180px)", styles)
        self.assertIn("minmax(370px,1.2fr)", styles)
        self.assertIn("designer-tools-column", editor)
        self.assertIn("product-config-column", editor)

    def test_deployment_templates_assets_and_wsgi_import_target(self):
        template_names={
            "_design_editor.html", "account_orders.html", "admin.html", "admin_login.html",
            "admin_order_detail.html", "cart.html", "checkout.html", "design.html", "index.html",
            "login.html", "order_detail.html", "products.html", "profile.html", "register.html",
        }
        self.assertEqual(set(website.app.jinja_env.list_templates()),template_names)
        for name in template_names:
            with self.subTest(template=name):
                self.assertTrue((website.BASE_DIR / "templates" / name).is_file())
                website.app.jinja_env.get_template(name)

        required_static=("style.css","design.css","design.js","design-3d-loader.js",
                         "design-3d.js","three.module.js","models/manifest.json",
                         "images/storefront-banner-reference.png","storefront-banner.svg")
        for name in required_static:
            with self.subTest(static=name):
                self.assertTrue((website.BASE_DIR / "static" / name).is_file())

        for path in ("/","/products","/design","/cart","/register","/login","/admin/login"):
            with self.subTest(path=path):
                response=self.request("GET",path)
                self.assertEqual(response.status_code,200)
                self.assertNotIn(b"TemplateNotFound",response.data)
        cart_add=self.request("POST","/cart/add",headers={"X-Requested-With":"XMLHttpRequest"},data={
            "product":"tshirt","size":"M","quantity":"1",
            "design_images":[(self.png("navy"),"deployment-smoke.png")],
        },content_type="multipart/form-data")
        self.assertEqual(cart_add.status_code,201,cart_add.get_data(as_text=True))
        checkout=self.request("GET","/checkout")
        self.assertEqual(checkout.status_code,200)
        self.assertIn(b"checkout-page",checkout.data)

        # Gunicorn resolves this WSGI target as module `app`, object `app`.
        self.assertEqual(website.app.import_name,"app")
        self.assertTrue(callable(website.app))
        self.assertEqual(website.app.template_folder,"templates")
        try:
            from gunicorn.util import import_app
        except ModuleNotFoundError as error:
            # Gunicorn's loader imports POSIX fcntl, which is unavailable on Windows.
            self.assertEqual(error.name,"fcntl")
            self.assertTrue(callable(website.app))
        else:
            self.assertTrue(callable(import_app("app:app")))
        render_config=(website.BASE_DIR / "render.yaml").read_text(encoding="utf-8")
        self.assertIn("startCommand: gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4",render_config)
        self.assertIn("gunicorn",(website.BASE_DIR / "requirements.txt").read_text(encoding="utf-8").splitlines())

    def test_production_debug_is_disabled_even_if_flask_debug_is_set(self):
        with patch.object(website,"IS_RENDER",True), patch.dict("os.environ",{"FLASK_DEBUG":"1"}):
            self.assertFalse(website.development_debug_enabled())
            self.assertFalse(website.app.debug)

    def test_all_eight_product_models_and_cart_checkout_paths(self):
        slugs = ("tshirt", "mug", "pillow", "banner", "frame", "phonecase", "poster", "gift")
        sizes = {"tshirt":"M", "mug":"11 oz", "pillow":"12 × 12 in", "banner":"2 × 3 ft",
                 "frame":"5 × 7 in", "phonecase":"iPhone", "poster":"A4", "gift":"Standard"}
        model_code = (website.BASE_DIR / "static" / "design-3d.js").read_text(encoding="utf-8")
        manifest = json.loads((website.BASE_DIR / "static" / "models" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(set(manifest["products"]), set(slugs))
        self.assertTrue(all("print_surfaces" in entry for entry in manifest["products"].values()))
        self.assertIn("shirtPrintPanel", model_code)
        self.assertIn("shirtRelief", model_code)
        self.assertIn("points.setZ(i,.13+this.shirtRelief(x,y))", model_code)
        self.assertIn("this.textureFor(region)", model_code)
        self.assertIn("No licensed product GLB assets are installed", (website.BASE_DIR / "static" / "models" / "README.md").read_text(encoding="utf-8"))
        for slug in slugs:
            with self.subTest(product=slug):
                self.assertIn(f"slug === '{slug}'", model_code)
                editor = self.request("GET", f"/design?product={slug}")
                self.assertEqual(editor.status_code, 200)
                response = self.request("POST", "/cart/add", headers={
                    "X-Requested-With": "XMLHttpRequest", "Accept": "application/json",
                }, data={
                    "product": slug, "size": sizes[slug], "quantity": "1",
                    "design_images": [(self.png("teal"), f"{slug}.png")],
                }, content_type="multipart/form-data")
                self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        with self.client.session_transaction() as session:
            self.assertEqual([item["slug"] for item in session["cart"]], list(slugs))
            self.assertTrue(all(item["design_mode"] == "create" for item in session["cart"]))
        checkout = self.request("POST", "/checkout", data={
            "customer_name": "All Product Test", "email": "models@example.com",
            "phone": "1234567890", "address": "Model test address",
        })
        self.assertEqual(checkout.status_code, 200)
        with closing(sqlite3.connect(website.DATABASE)) as db:
            row = db.execute("SELECT items_json FROM orders").fetchone()
        self.assertEqual([item["slug"] for item in json.loads(row[0])], list(slugs))
        self.assertIn("this.targetRotation", model_code)
        self.assertIn("new THREE.MeshPhysicalMaterial", model_code)
        self.assertIn("this.stitch(", model_code)
        self.assertIn("new THREE.TorusGeometry(.645,.027", model_code)
        self.assertIn("new THREE.LineDashedMaterial", model_code)
        self.assertIn("this.detailTexture('flex')", model_code)
        self.assertIn("const glass=this.addMesh", model_code)
        self.assertIn("shape.holes.push(cutout)", model_code)
        self.assertIn("this.detailTexture('paper')", model_code)
        self.assertIn("this.detailTexture('cardboard')", model_code)


if __name__ == "__main__":
    unittest.main()
