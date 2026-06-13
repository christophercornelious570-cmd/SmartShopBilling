"""
╔══════════════════════════════════════════════════════╗
║         SMART SHOP BILLING SYSTEM - Python           ║
║  Features: SQLite DB, PDF Export, Inventory,         ║
║  Auto Bill No, GST, Discount, WhatsApp, Reports,     ║
║  Barcode Scanning, Customer DB, Loyalty Points       ║
╚══════════════════════════════════════════════════════╝

Requirements (install once):
    pip install reportlab pillow pyzbar

    Linux also needs:  sudo apt install libzbar0
    macOS also needs:  brew install zbar

Run:
    python billing_app.py
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import sqlite3
import os
import datetime
import webbrowser
import urllib.parse
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                Paragraph, Spacer, HRFlowable)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT

try:
    from pyzbar import pyzbar
    import cv2
    PYZBAR_AVAILABLE = True
except ImportError:
    PYZBAR_AVAILABLE = False

# ─────────────────────────────────────────────
#  SHOP CONFIGURATION  ← Edit these!
# ─────────────────────────────────────────────
SHOP_NAME    = "Sri Murugan Stores"
SHOP_ADDRESS = "No. 12, Anna Nagar, Chennai - 600040"
SHOP_PHONE   = "+91 98765 43210"
SHOP_GST_NO  = "33AABCS1429B1ZS"
GST_RATE     = 0.05   # 5%

# Loyalty: earn 1 point per ₹LOYALTY_EARN_PER spent; redeem ₹LOYALTY_VALUE per point
LOYALTY_EARN_PER  = 50    # ₹50 spent → 1 point
LOYALTY_VALUE     = 0.50  # 1 point = ₹0.50 off

DB_FILE = os.path.join(os.path.dirname(__file__), "billing.db")


# ══════════════════════════════════════════════
#  DATABASE LAYER
# ══════════════════════════════════════════════
class Database:
    def __init__(self):
        self.conn = sqlite3.connect(DB_FILE)
        self._create_tables()

    def _create_tables(self):
        cur = self.conn.cursor()
        cur.executescript("""
            CREATE TABLE IF NOT EXISTS inventory (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                name     TEXT    NOT NULL UNIQUE,
                price    REAL    NOT NULL,
                stock    INTEGER NOT NULL DEFAULT 0,
                category TEXT    DEFAULT 'General',
                barcode  TEXT    DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS customers (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                name           TEXT    NOT NULL,
                phone          TEXT    NOT NULL UNIQUE,
                email          TEXT    DEFAULT '',
                address        TEXT    DEFAULT '',
                loyalty_points INTEGER NOT NULL DEFAULT 0,
                total_spent    REAL    NOT NULL DEFAULT 0.0,
                joined_on      TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS bills (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                bill_no      TEXT    NOT NULL UNIQUE,
                customer     TEXT,
                phone        TEXT,
                subtotal     REAL,
                discount_pct REAL,
                discount_amt REAL,
                gst_amt      REAL,
                total        REAL,
                payment      TEXT,
                points_earned   INTEGER DEFAULT 0,
                points_redeemed INTEGER DEFAULT 0,
                created_at   TEXT
            );

            CREATE TABLE IF NOT EXISTS bill_items (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                bill_no  TEXT,
                item     TEXT,
                qty      INTEGER,
                price    REAL,
                amount   REAL
            );
        """)

        # ── Migrations (safe to run on existing DB) ──
        migrations = [
            "ALTER TABLE inventory ADD COLUMN barcode TEXT DEFAULT ''",
            "ALTER TABLE bills ADD COLUMN points_earned INTEGER DEFAULT 0",
            "ALTER TABLE bills ADD COLUMN points_redeemed INTEGER DEFAULT 0",
        ]
        for sql in migrations:
            try:
                cur.execute(sql)
                self.conn.commit()
            except sqlite3.OperationalError:
                pass

        self.conn.commit()

        # Seed sample inventory if empty
        if not cur.execute("SELECT COUNT(*) FROM inventory").fetchone()[0]:
            samples = [
                ("Rice (1kg)",    55.00, 100, "Grocery",       "8901058851058"),
                ("Sugar (1kg)",   42.00,  80, "Grocery",       "8901058851065"),
                ("Toor Dal",      98.00,  60, "Grocery",       "8901058851072"),
                ("Sunflower Oil", 140.00, 40, "Grocery",       "8901058851089"),
                ("Milk (1L)",     25.00, 150, "Dairy",         "8901058851096"),
                ("Butter",        55.00,  50, "Dairy",         "8901058851102"),
                ("Bread",         40.00,  30, "Bakery",        "8901058851119"),
                ("Biscuits",      20.00, 200, "Snacks",        "8901058851126"),
                ("Soap",          35.00,  80, "Personal Care", "8901058851133"),
                ("Shampoo",       90.00,  40, "Personal Care", "8901058851140"),
            ]
            cur.executemany(
                "INSERT INTO inventory(name,price,stock,category,barcode) VALUES(?,?,?,?,?)",
                samples)
            self.conn.commit()

    # ── Inventory ──
    def get_items(self):
        return self.conn.execute(
            "SELECT name, price, stock, category, barcode FROM inventory ORDER BY name"
        ).fetchall()

    def get_item_names(self):
        return [r[0] for r in self.conn.execute(
            "SELECT name FROM inventory ORDER BY name")]

    def get_item_price(self, name):
        r = self.conn.execute(
            "SELECT price FROM inventory WHERE name=?", (name,)).fetchone()
        return r[0] if r else 0.0

    def get_item_stock(self, name):
        r = self.conn.execute(
            "SELECT stock FROM inventory WHERE name=?", (name,)).fetchone()
        return r[0] if r else 0

    def lookup_by_barcode(self, barcode):
        r = self.conn.execute(
            "SELECT name, price, stock FROM inventory WHERE barcode=?",
            (barcode.strip(),)).fetchone()
        return r

    def add_item(self, name, price, stock, category, barcode=""):
        try:
            self.conn.execute(
                "INSERT INTO inventory(name,price,stock,category,barcode) VALUES(?,?,?,?,?)",
                (name, price, stock, category, barcode))
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def update_stock(self, name, qty_sold):
        self.conn.execute(
            "UPDATE inventory SET stock = stock - ? WHERE name=?",
            (qty_sold, name))
        self.conn.commit()

    def update_item(self, old_name, name, price, stock, category, barcode=""):
        self.conn.execute(
            "UPDATE inventory SET name=?,price=?,stock=?,category=?,barcode=? WHERE name=?",
            (name, price, stock, category, barcode, old_name))
        self.conn.commit()

    def delete_item(self, name):
        self.conn.execute("DELETE FROM inventory WHERE name=?", (name,))
        self.conn.commit()

    # ── Customers ──
    def get_customer_by_phone(self, phone):
        """Return customer row or None."""
        return self.conn.execute(
            "SELECT * FROM customers WHERE phone=?", (phone.strip(),)).fetchone()

    def get_all_customers(self):
        return self.conn.execute(
            """SELECT name, phone, email, loyalty_points, total_spent, joined_on
               FROM customers ORDER BY name""").fetchall()

    def search_customers(self, query):
        q = f"%{query}%"
        return self.conn.execute(
            """SELECT name, phone, email, loyalty_points, total_spent, joined_on
               FROM customers WHERE name LIKE ? OR phone LIKE ?
               ORDER BY name""", (q, q)).fetchall()

    def upsert_customer(self, name, phone, email="", address=""):
        """Insert new customer or update name/email/address if phone exists."""
        existing = self.get_customer_by_phone(phone)
        if existing:
            self.conn.execute(
                "UPDATE customers SET name=?, email=?, address=? WHERE phone=?",
                (name, email, address, phone))
        else:
            now = datetime.date.today().isoformat()
            self.conn.execute(
                """INSERT INTO customers(name,phone,email,address,loyalty_points,total_spent,joined_on)
                   VALUES(?,?,?,?,0,0.0,?)""",
                (name, phone, email, address, now))
        self.conn.commit()

    def update_customer_loyalty(self, phone, points_delta, spent_delta):
        """Adjust loyalty_points and total_spent for a customer."""
        self.conn.execute(
            """UPDATE customers
               SET loyalty_points = loyalty_points + ?,
                   total_spent    = total_spent    + ?
               WHERE phone=?""",
            (points_delta, spent_delta, phone))
        self.conn.commit()

    def redeem_points(self, phone, points_to_redeem):
        """Deduct redeemed points. Returns False if insufficient."""
        cust = self.get_customer_by_phone(phone)
        if not cust or cust[6] < points_to_redeem:   # col 6 = loyalty_points
            return False
        self.conn.execute(
            "UPDATE customers SET loyalty_points = loyalty_points - ? WHERE phone=?",
            (points_to_redeem, phone))
        self.conn.commit()
        return True

    def get_customer_bills(self, phone):
        """All bills for a customer phone, newest first."""
        return self.conn.execute(
            """SELECT bill_no, customer, total, payment, points_earned,
                      points_redeemed, created_at
               FROM bills WHERE phone=? ORDER BY created_at DESC""",
            (phone,)).fetchall()

    def delete_customer(self, phone):
        self.conn.execute("DELETE FROM customers WHERE phone=?", (phone,))
        self.conn.commit()

    def update_customer_details(self, old_phone, name, phone, email, address):
        self.conn.execute(
            "UPDATE customers SET name=?,phone=?,email=?,address=? WHERE phone=?",
            (name, phone, email, address, old_phone))
        self.conn.commit()

    # ── Bills ──
    def next_bill_no(self):
        today = datetime.date.today()
        prefix = f"INV-{today.year}{today.month:02d}{today.day:02d}-"
        r = self.conn.execute(
            "SELECT COUNT(*) FROM bills WHERE bill_no LIKE ?",
            (prefix + "%",)).fetchone()[0]
        return f"{prefix}{r+1:03d}"

    def save_bill(self, bill_no, customer, phone, items,
                  subtotal, disc_pct, disc_amt, gst_amt, total, payment,
                  points_earned=0, points_redeemed=0):
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.conn.execute(
            """INSERT INTO bills(bill_no,customer,phone,subtotal,
               discount_pct,discount_amt,gst_amt,total,payment,
               points_earned,points_redeemed,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (bill_no, customer, phone, subtotal,
             disc_pct, disc_amt, gst_amt, total, payment,
             points_earned, points_redeemed, now))
        for item, qty, price, amount in items:
            self.conn.execute(
                "INSERT INTO bill_items(bill_no,item,qty,price,amount) VALUES(?,?,?,?,?)",
                (bill_no, item, qty, price, amount))
            self.update_stock(item, qty)
        # Update customer stats if phone provided
        if phone:
            self.update_customer_loyalty(phone, points_earned - points_redeemed, total)
        self.conn.commit()

    def get_bills(self, days=30):
        since = (datetime.date.today() -
                 datetime.timedelta(days=days)).strftime("%Y-%m-%d")
        return self.conn.execute(
            """SELECT bill_no,customer,phone,total,payment,created_at
               FROM bills WHERE created_at >= ? ORDER BY created_at DESC""",
            (since,)).fetchall()

    def search_bills_by_phone(self, phone):
        return self.conn.execute(
            """SELECT bill_no,customer,phone,total,payment,created_at
               FROM bills WHERE phone LIKE ? ORDER BY created_at DESC""",
            (f"%{phone}%",)).fetchall()

    def get_bill_items(self, bill_no):
        return self.conn.execute(
            "SELECT item,qty,price,amount FROM bill_items WHERE bill_no=?",
            (bill_no,)).fetchall()

    def get_bill_header(self, bill_no):
        return self.conn.execute(
            "SELECT * FROM bills WHERE bill_no=?", (bill_no,)).fetchone()

    def sales_summary(self, days=30):
        since = (datetime.date.today() -
                 datetime.timedelta(days=days)).strftime("%Y-%m-%d")
        totals = self.conn.execute(
            """SELECT COUNT(*), SUM(total), SUM(gst_amt), SUM(discount_amt)
               FROM bills WHERE created_at >= ?""", (since,)).fetchone()
        top_items = self.conn.execute(
            """SELECT bi.item, SUM(bi.qty) as qty, SUM(bi.amount) as rev
               FROM bill_items bi
               JOIN bills b ON b.bill_no=bi.bill_no
               WHERE b.created_at >= ?
               GROUP BY bi.item ORDER BY rev DESC LIMIT 5""",
            (since,)).fetchall()
        payment_split = self.conn.execute(
            """SELECT payment, COUNT(*), SUM(total)
               FROM bills WHERE created_at >= ?
               GROUP BY payment""", (since,)).fetchall()
        return totals, top_items, payment_split


# ══════════════════════════════════════════════
#  PDF GENERATOR
# ══════════════════════════════════════════════
class PDFGenerator:
    @staticmethod
    def generate(bill_no, customer, phone, items,
                 subtotal, disc_pct, disc_amt, gst_amt, total,
                 payment, output_path,
                 points_earned=0, points_redeemed=0, loyalty_points=None):
        doc = SimpleDocTemplate(output_path, pagesize=A4,
                                topMargin=15*mm, bottomMargin=15*mm,
                                leftMargin=20*mm, rightMargin=20*mm)
        styles = getSampleStyleSheet()
        story  = []

        h1 = ParagraphStyle("h1", fontSize=18, fontName="Helvetica-Bold",
                             alignment=TA_CENTER, textColor=colors.HexColor("#1a237e"))
        h2 = ParagraphStyle("h2", fontSize=10, fontName="Helvetica",
                             alignment=TA_CENTER, textColor=colors.grey)
        story.append(Paragraph(SHOP_NAME, h1))
        story.append(Paragraph(SHOP_ADDRESS, h2))
        story.append(Paragraph(f"Phone: {SHOP_PHONE}  |  GSTIN: {SHOP_GST_NO}", h2))
        story.append(HRFlowable(width="100%", thickness=2,
                                color=colors.HexColor("#1a237e")))
        story.append(Spacer(1, 4*mm))

        now = datetime.datetime.now().strftime("%d-%m-%Y %H:%M")
        info_data = [
            [Paragraph(f"<b>Bill No:</b> {bill_no}", styles["Normal"]),
             Paragraph(f"<b>Date:</b> {now}", styles["Normal"])],
            [Paragraph(f"<b>Customer:</b> {customer or 'Walk-in'}", styles["Normal"]),
             Paragraph(f"<b>Phone:</b> {phone or '-'}", styles["Normal"])],
        ]
        if loyalty_points is not None:
            info_data.append([
                Paragraph(f"<b>Points Earned this bill:</b> {points_earned}", styles["Normal"]),
                Paragraph(f"<b>Total Loyalty Points:</b> {loyalty_points}", styles["Normal"]),
            ])
        info_table = Table(info_data, colWidths=[90*mm, 80*mm])
        info_table.setStyle(TableStyle([("FONTSIZE", (0,0), (-1,-1), 10)]))
        story.append(info_table)
        story.append(Spacer(1, 4*mm))
        story.append(HRFlowable(width="100%", thickness=1, color=colors.lightgrey))
        story.append(Spacer(1, 2*mm))

        col_hdr = ParagraphStyle("ch", fontName="Helvetica-Bold",
                                 fontSize=10, alignment=TA_CENTER,
                                 textColor=colors.white)
        item_data = [[
            Paragraph("ITEM", col_hdr),
            Paragraph("QTY", col_hdr),
            Paragraph("UNIT PRICE", col_hdr),
            Paragraph("AMOUNT", col_hdr),
        ]]
        for itm, qty, price, amount in items:
            item_data.append([itm, str(qty), f"₹{price:.2f}", f"₹{amount:.2f}"])

        item_table = Table(item_data, colWidths=[80*mm, 20*mm, 40*mm, 30*mm])
        item_table.setStyle(TableStyle([
            ("BACKGROUND",  (0, 0), (-1, 0), colors.HexColor("#1a237e")),
            ("TEXTCOLOR",   (0, 0), (-1, 0), colors.white),
            ("FONTNAME",    (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",    (0, 0), (-1,-1), 10),
            ("ROWBACKGROUNDS", (0,1), (-1,-1),
             [colors.white, colors.HexColor("#e8eaf6")]),
            ("ALIGN",       (1, 0), (-1,-1), "CENTER"),
            ("GRID",        (0, 0), (-1,-1), 0.5, colors.lightgrey),
            ("TOPPADDING",  (0, 0), (-1,-1), 4),
            ("BOTTOMPADDING",(0,0), (-1,-1), 4),
        ]))
        story.append(item_table)
        story.append(Spacer(1, 4*mm))

        summary = [
            ["Subtotal:",               f"₹{subtotal:.2f}"],
            [f"Discount ({disc_pct}%):", f"-₹{disc_amt:.2f}"],
            [f"GST ({int(GST_RATE*100)}%):", f"₹{gst_amt:.2f}"],
        ]
        if points_redeemed:
            redeem_val = points_redeemed * LOYALTY_VALUE
            summary.append([f"Points Redeemed ({points_redeemed} pts):", f"-₹{redeem_val:.2f}"])
        summary += [
            ["TOTAL:",          f"₹{total:.2f}"],
            ["Payment Method:", payment],
        ]
        total_row = len(summary) - 2
        sum_styles = [
            ("FONTSIZE",  (0,0), (-1,-1), 10),
            ("ALIGN",     (1,0), (-1,-1), "RIGHT"),
            ("LINEABOVE", (0, total_row), (-1, total_row), 1.5, colors.HexColor("#1a237e")),
            ("FONTNAME",  (0, total_row), (-1, total_row), "Helvetica-Bold"),
            ("TEXTCOLOR", (0, total_row), (-1, total_row), colors.HexColor("#1a237e")),
            ("FONTSIZE",  (0, total_row), (-1, total_row), 12),
        ]
        sum_table = Table(summary, colWidths=[120*mm, 50*mm])
        sum_table.setStyle(TableStyle(sum_styles))
        story.append(sum_table)
        story.append(Spacer(1, 6*mm))
        story.append(HRFlowable(width="100%", thickness=1,
                                color=colors.HexColor("#1a237e")))

        footer = ParagraphStyle("ft", fontSize=10, fontName="Helvetica-Bold",
                                alignment=TA_CENTER,
                                textColor=colors.HexColor("#1a237e"))
        story.append(Spacer(1, 4*mm))
        if points_earned:
            story.append(Paragraph(
                f"🎁  You earned {points_earned} loyalty points this visit!", footer))
        story.append(Paragraph("Thank you for shopping with us! 🙏", footer))
        story.append(Paragraph("Visit Again!", footer))

        doc.build(story)
        return output_path


# ══════════════════════════════════════════════
#  MAIN APPLICATION
# ══════════════════════════════════════════════
class BillingApp(tk.Tk):
    COLORS = {
        "bg":        "#f0f4ff",
        "primary":   "#1a237e",
        "accent":    "#3949ab",
        "success":   "#2e7d32",
        "danger":    "#c62828",
        "warn":      "#e65100",
        "header_bg": "#1a237e",
        "header_fg": "#ffffff",
        "row_even":  "#e8eaf6",
        "row_odd":   "#ffffff",
        "bill_bg":   "#fffde7",
        "scan_bg":   "#e8f5e9",
        "loyalty_bg":"#fff3e0",   # orange-tint for loyalty area
        "cust_bg":   "#e8eaf6",
    }

    def __init__(self):
        super().__init__()
        self.db = Database()
        self.cart = []
        self.bill_generated = False
        self._scan_buffer = ""
        self._scan_timer  = None
        self._redeem_points = 0   # points chosen to redeem this bill

        self.title(f"Smart Billing System — {SHOP_NAME}")
        self.geometry("1180x860")
        self.configure(bg=self.COLORS["bg"])
        self.resizable(True, True)

        self._apply_styles()
        self._build_ui()
        self._refresh_item_list()

    # ── Styles ──
    def _apply_styles(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        bg, pri, acc = self.COLORS["bg"], self.COLORS["primary"], self.COLORS["accent"]

        s.configure("TFrame",      background=bg)
        s.configure("TLabel",      background=bg, foreground="#212121", font=("Segoe UI", 10))
        s.configure("Header.TLabel", background=self.COLORS["header_bg"],
                    foreground=self.COLORS["header_fg"],
                    font=("Segoe UI", 13, "bold"))
        s.configure("Title.TLabel",  background=bg,
                    foreground=pri, font=("Segoe UI", 11, "bold"))
        s.configure("Loyalty.TLabel", background=self.COLORS["loyalty_bg"],
                    foreground="#e65100", font=("Segoe UI", 10, "bold"))
        s.configure("TEntry",      fieldbackground="white", font=("Segoe UI", 10))
        s.configure("TCombobox",   fieldbackground="white", font=("Segoe UI", 10))

        for name, color in [("Primary.TButton", pri),
                             ("Success.TButton", self.COLORS["success"]),
                             ("Danger.TButton",  self.COLORS["danger"]),
                             ("Warn.TButton",    self.COLORS["warn"])]:
            s.configure(name, background=color, foreground="white",
                        font=("Segoe UI", 10, "bold"), padding=6,
                        relief="flat", borderwidth=0)
            s.map(name, background=[("active", acc)])

        s.configure("Treeview",
                    font=("Segoe UI", 10), rowheight=26,
                    fieldbackground="white", background="white")
        s.configure("Treeview.Heading",
                    font=("Segoe UI", 10, "bold"),
                    background=pri, foreground="white")
        s.map("Treeview", background=[("selected", acc)])

    # ── UI Build ──
    def _build_ui(self):
        hdr = tk.Frame(self, bg=self.COLORS["header_bg"], height=60)
        hdr.pack(fill="x")
        tk.Label(hdr, text=f"🏪  {SHOP_NAME}",
                 bg=self.COLORS["header_bg"], fg="white",
                 font=("Segoe UI", 16, "bold")).pack(side="left", padx=20, pady=10)
        tk.Label(hdr, text=SHOP_ADDRESS,
                 bg=self.COLORS["header_bg"], fg="#c5cae9",
                 font=("Segoe UI", 9)).pack(side="left", padx=5)

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=8)

        self.tab_billing   = ttk.Frame(nb)
        self.tab_customers = ttk.Frame(nb)
        self.tab_inventory = ttk.Frame(nb)
        self.tab_history   = ttk.Frame(nb)
        self.tab_reports   = ttk.Frame(nb)

        nb.add(self.tab_billing,   text="  🧾 Billing  ")
        nb.add(self.tab_customers, text="  👥 Customers  ")
        nb.add(self.tab_inventory, text="  📦 Inventory  ")
        nb.add(self.tab_history,   text="  📋 Bill History  ")
        nb.add(self.tab_reports,   text="  📊 Reports  ")

        nb.bind("<<NotebookTabChanged>>", self._on_tab_change)

        self._build_billing_tab()
        self._build_customers_tab()
        self._build_inventory_tab()
        self._build_history_tab()
        self._build_reports_tab()

    # ════════════════════════════════════════
    #  TAB 1 — BILLING
    # ════════════════════════════════════════
    def _build_billing_tab(self):
        p = self.tab_billing
        p.columnconfigure(0, weight=1)
        p.columnconfigure(1, weight=1)
        p.rowconfigure(4, weight=1)

        # ── Bill No & Date ──
        info_f = ttk.Frame(p)
        info_f.grid(row=0, column=0, columnspan=2, sticky="ew", padx=8, pady=(8,0))
        self.lbl_bill_no = ttk.Label(info_f, text="Bill No: —", style="Title.TLabel")
        self.lbl_bill_no.pack(side="left", padx=10)
        self.lbl_date = ttk.Label(info_f,
            text=datetime.datetime.now().strftime("Date: %d-%m-%Y  %H:%M"),
            style="Title.TLabel")
        self.lbl_date.pack(side="right", padx=10)
        self._refresh_bill_no()

        # ── Customer Info ──
        cf = ttk.LabelFrame(p, text=" Customer Info ", padding=8)
        cf.grid(row=1, column=0, columnspan=2, sticky="ew", padx=8, pady=4)
        for c in (1,3,5): cf.columnconfigure(c, weight=1)

        ttk.Label(cf, text="Name:").grid(row=0, column=0, sticky="w", padx=4)
        self.ent_customer = ttk.Entry(cf)
        self.ent_customer.grid(row=0, column=1, sticky="ew", padx=4)

        ttk.Label(cf, text="Phone:").grid(row=0, column=2, sticky="w", padx=4)
        self.ent_phone = ttk.Entry(cf)
        self.ent_phone.grid(row=0, column=3, sticky="ew", padx=4)
        self.ent_phone.bind("<FocusOut>", self._lookup_customer_on_phone)
        self.ent_phone.bind("<Return>",   self._lookup_customer_on_phone)

        ttk.Label(cf, text="Payment:").grid(row=0, column=4, sticky="w", padx=4)
        self.cmb_payment = ttk.Combobox(cf, values=["Cash","UPI","Card","Credit"],
                                        state="readonly", width=10)
        self.cmb_payment.current(0)
        self.cmb_payment.grid(row=0, column=5, sticky="ew", padx=4)

        ttk.Label(cf, text="Discount %:").grid(row=0, column=6, sticky="w", padx=4)
        self.ent_discount = ttk.Entry(cf, width=6)
        self.ent_discount.insert(0, "0")
        self.ent_discount.grid(row=0, column=7, padx=4)

        # ── LOYALTY PANEL ──────────────────────────────────────────────────────
        loy_f = tk.Frame(p, bg=self.COLORS["loyalty_bg"], bd=1, relief="groove")
        loy_f.grid(row=2, column=0, columnspan=2, sticky="ew", padx=8, pady=2)

        tk.Label(loy_f, text="🎁  Loyalty Points:",
                 bg=self.COLORS["loyalty_bg"], fg="#e65100",
                 font=("Segoe UI", 10, "bold")).pack(side="left", padx=8, pady=5)

        self.lbl_loyalty = tk.Label(loy_f, text="— points available",
                                    bg=self.COLORS["loyalty_bg"], fg="#e65100",
                                    font=("Segoe UI", 10))
        self.lbl_loyalty.pack(side="left", padx=4)

        tk.Label(loy_f, text="  Redeem:",
                 bg=self.COLORS["loyalty_bg"], fg="#555",
                 font=("Segoe UI", 9)).pack(side="left", padx=4)
        self.ent_redeem = ttk.Entry(loy_f, width=7)
        self.ent_redeem.insert(0, "0")
        self.ent_redeem.pack(side="left")

        tk.Label(loy_f, text="pts",
                 bg=self.COLORS["loyalty_bg"], fg="#555",
                 font=("Segoe UI", 9)).pack(side="left", padx=2)

        ttk.Button(loy_f, text="Apply",
                   style="Warn.TButton",
                   command=self._apply_redeem).pack(side="left", padx=6)

        self.lbl_redeem_status = tk.Label(loy_f, text="",
                                          bg=self.COLORS["loyalty_bg"],
                                          font=("Segoe UI", 9, "bold"))
        self.lbl_redeem_status.pack(side="left", padx=8)

        tk.Label(loy_f,
                 text=f"(Earn 1 pt per ₹{LOYALTY_EARN_PER} spent  |  1 pt = ₹{LOYALTY_VALUE:.2f} off)",
                 bg=self.COLORS["loyalty_bg"], fg="#888",
                 font=("Segoe UI", 8, "italic")).pack(side="right", padx=10)
        # ──────────────────────────────────────────────────────────────────────

        # ── BARCODE SCANNER ROW ──
        scan_f = tk.Frame(p, bg=self.COLORS["scan_bg"], bd=1, relief="groove")
        scan_f.grid(row=3, column=0, columnspan=2, sticky="ew", padx=8, pady=2)

        tk.Label(scan_f, text="📷  Barcode Scanner:",
                 bg=self.COLORS["scan_bg"], fg=self.COLORS["success"],
                 font=("Segoe UI", 10, "bold")).pack(side="left", padx=8, pady=5)

        self.ent_barcode = ttk.Entry(scan_f, width=24, font=("Consolas", 11))
        self.ent_barcode.pack(side="left", padx=4, pady=5)
        self.ent_barcode.bind("<Return>",     self._on_barcode_enter)
        self.ent_barcode.bind("<KP_Enter>",   self._on_barcode_enter)
        self.ent_barcode.bind("<KeyRelease>", self._on_barcode_keyrelease)

        ttk.Button(scan_f, text="🔍 Look Up",
                   style="Success.TButton",
                   command=self._lookup_barcode_manual).pack(side="left", padx=4)
        ttk.Button(scan_f, text="🗑 Clear",
                   style="Danger.TButton",
                   command=lambda: self.ent_barcode.delete(0, "end")).pack(side="left", padx=2)

        tk.Label(scan_f,
                 text="Tip: Click this field and scan any product barcode.",
                 bg=self.COLORS["scan_bg"], fg="#555",
                 font=("Segoe UI", 8, "italic")).pack(side="left", padx=12)

        self.lbl_scan_status = tk.Label(scan_f, text="",
                                        bg=self.COLORS["scan_bg"],
                                        font=("Segoe UI", 9, "bold"))
        self.lbl_scan_status.pack(side="right", padx=10)

        # ── LEFT: Add Item ──
        add_f = ttk.LabelFrame(p, text=" Add Item to Cart ", padding=8)
        add_f.grid(row=4, column=0, sticky="nsew", padx=(8,4), pady=4)
        add_f.columnconfigure(1, weight=1)

        ttk.Label(add_f, text="Item Name:").grid(row=0, column=0, sticky="w", pady=3)
        self.cmb_item = ttk.Combobox(add_f)
        self.cmb_item.grid(row=0, column=1, sticky="ew", pady=3, padx=4)
        self.cmb_item.bind("<<ComboboxSelected>>", self._autofill_price)
        self.cmb_item.bind("<KeyRelease>", self._filter_items)

        ttk.Label(add_f, text="Quantity:").grid(row=1, column=0, sticky="w", pady=3)
        self.ent_qty = ttk.Entry(add_f)
        self.ent_qty.grid(row=1, column=1, sticky="ew", pady=3, padx=4)

        ttk.Label(add_f, text="Unit Price (₹):").grid(row=2, column=0, sticky="w", pady=3)
        self.ent_price = ttk.Entry(add_f)
        self.ent_price.grid(row=2, column=1, sticky="ew", pady=3, padx=4)

        self.lbl_stock = ttk.Label(add_f, text="Stock: —", foreground="#555")
        self.lbl_stock.grid(row=3, column=0, columnspan=2, sticky="w", pady=2)

        ttk.Button(add_f, text="➕  Add to Cart",
                   style="Success.TButton",
                   command=self._add_item).grid(
            row=4, column=0, columnspan=2, sticky="ew", pady=6)

        ttk.Label(add_f, text="Cart:", style="Title.TLabel").grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(6,2))

        cart_cols = ("item","qty","price","amount")
        self.tree_cart = ttk.Treeview(add_f, columns=cart_cols,
                                      show="headings", height=7)
        for col, hdr, w in [("item","Item",160),("qty","Qty",50),
                             ("price","Price",80),("amount","Amount",90)]:
            self.tree_cart.heading(col, text=hdr)
            self.tree_cart.column(col, width=w, anchor="center")
        self.tree_cart.grid(row=6, column=0, columnspan=2, sticky="ew")

        ttk.Button(add_f, text="🗑  Remove Selected",
                   style="Danger.TButton",
                   command=self._remove_item).grid(
            row=7, column=0, columnspan=2, sticky="ew", pady=4)

        # ── RIGHT: Bill Preview ──
        bill_f = ttk.LabelFrame(p, text=" Bill Preview ", padding=8)
        bill_f.grid(row=4, column=1, sticky="nsew", padx=(4,8), pady=4)
        bill_f.rowconfigure(0, weight=1)
        bill_f.columnconfigure(0, weight=1)

        self.txt_bill = tk.Text(bill_f, font=("Consolas", 10),
                                state="disabled", wrap="none",
                                bg=self.COLORS["bill_bg"],
                                relief="flat", borderwidth=1)
        self.txt_bill.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(bill_f, command=self.txt_bill.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.txt_bill.configure(yscrollcommand=sb.set)

        # ── Action Buttons ──
        btn_f = ttk.Frame(p)
        btn_f.grid(row=5, column=0, columnspan=2, sticky="ew",
                   padx=8, pady=(0,8))
        for col in range(4): btn_f.columnconfigure(col, weight=1)

        ttk.Button(btn_f, text="✅  Generate Bill",
                   style="Primary.TButton",
                   command=self._generate_bill).grid(
            row=0, column=0, sticky="ew", padx=4)
        ttk.Button(btn_f, text="📄  Export PDF",
                   style="Warn.TButton",
                   command=self._export_pdf).grid(
            row=0, column=1, sticky="ew", padx=4)
        ttk.Button(btn_f, text="📲  WhatsApp",
                   style="Success.TButton",
                   command=self._send_whatsapp).grid(
            row=0, column=2, sticky="ew", padx=4)
        ttk.Button(btn_f, text="🔄  New Bill",
                   style="Danger.TButton",
                   command=self._new_bill).grid(
            row=0, column=3, sticky="ew", padx=4)

        self._update_bill_preview()

    # ════════════════════════════════════════
    #  TAB 2 — CUSTOMERS
    # ════════════════════════════════════════
    def _build_customers_tab(self):
        p = self.tab_customers
        p.rowconfigure(2, weight=1)
        p.columnconfigure(0, weight=3)
        p.columnconfigure(1, weight=2)

        # ── Search bar ──
        sf = ttk.Frame(p)
        sf.grid(row=0, column=0, columnspan=2, sticky="ew", padx=8, pady=8)
        ttk.Label(sf, text="🔍  Search by name or phone:").pack(side="left")
        self.ent_cust_search = ttk.Entry(sf, width=28)
        self.ent_cust_search.pack(side="left", padx=6)
        self.ent_cust_search.bind("<KeyRelease>", self._search_customers)
        ttk.Button(sf, text="Show All", style="Primary.TButton",
                   command=self._load_customers).pack(side="left", padx=4)
        ttk.Button(sf, text="📋 View Purchase History", style="Warn.TButton",
                   command=self._view_customer_history).pack(side="left", padx=8)

        # ── Add / Edit Form ──
        form = ttk.LabelFrame(p, text=" Add / Edit Customer ", padding=10)
        form.grid(row=1, column=0, columnspan=2, sticky="ew", padx=8, pady=4)
        form.columnconfigure((1,3,5,7), weight=1)

        fields = [("Name *:", "cust_name"), ("Phone *:", "cust_phone"),
                  ("Email:",  "cust_email"), ("Address:", "cust_address")]
        self._cust_vars = {}
        for i, (lbl, key) in enumerate(fields):
            ttk.Label(form, text=lbl).grid(row=0, column=i*2, sticky="w", padx=4)
            v = ttk.Entry(form)
            v.grid(row=0, column=i*2+1, sticky="ew", padx=4)
            self._cust_vars[key] = v

        btn_row = ttk.Frame(form)
        btn_row.grid(row=1, column=0, columnspan=8, sticky="ew", pady=(8,0))
        for c in range(3): btn_row.columnconfigure(c, weight=1)
        ttk.Button(btn_row, text="➕ Add Customer", style="Success.TButton",
                   command=self._cust_add).grid(row=0, column=0, sticky="ew", padx=4)
        ttk.Button(btn_row, text="✏️ Update Selected", style="Primary.TButton",
                   command=self._cust_update).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(btn_row, text="🗑 Delete Selected", style="Danger.TButton",
                   command=self._cust_delete).grid(row=0, column=2, sticky="ew", padx=4)

        # ── Customer Table ──
        cols = ("name","phone","email","points","spent","joined")
        self.tree_cust = ttk.Treeview(p, columns=cols, show="headings")
        for col, hdr, w in [("name","Name",170),("phone","Phone",120),
                             ("email","Email",160),("points","🎁 Points",80),
                             ("spent","Total Spent",110),("joined","Joined",100)]:
            self.tree_cust.heading(col, text=hdr)
            self.tree_cust.column(col, width=w, anchor="center")
        self.tree_cust.tag_configure("gold",  foreground="#b8860b")  # high spender
        self.tree_cust.tag_configure("loyal", foreground="#1a237e")
        self.tree_cust.grid(row=2, column=0, columnspan=2,
                            sticky="nsew", padx=8, pady=(0,8))
        self.tree_cust.bind("<<TreeviewSelect>>", self._cust_select)
        sb_c = ttk.Scrollbar(p, command=self.tree_cust.yview)
        sb_c.grid(row=2, column=2, sticky="ns", pady=(0,8))
        self.tree_cust.configure(yscrollcommand=sb_c.set)

    # ════════════════════════════════════════
    #  TAB 3 — INVENTORY
    # ════════════════════════════════════════
    def _build_inventory_tab(self):
        p = self.tab_inventory
        p.rowconfigure(1, weight=1)
        p.columnconfigure(0, weight=1)

        form = ttk.LabelFrame(p, text=" Add / Edit Item ", padding=10)
        form.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        form.columnconfigure((1,3,5,7,9), weight=1)

        fields = [("Name:", "inv_name"), ("Price (₹):", "inv_price"),
                  ("Stock:", "inv_stock"), ("Category:", "inv_category"),
                  ("Barcode:", "inv_barcode")]
        self._inv_vars = {}
        for i, (lbl, key) in enumerate(fields):
            ttk.Label(form, text=lbl).grid(row=0, column=i*2, sticky="w", padx=4)
            v = ttk.Entry(form)
            v.grid(row=0, column=i*2+1, sticky="ew", padx=4)
            self._inv_vars[key] = v

        btn_row = ttk.Frame(form)
        btn_row.grid(row=1, column=0, columnspan=10, sticky="ew", pady=(8,0))
        for col in range(3): btn_row.columnconfigure(col, weight=1)
        ttk.Button(btn_row, text="➕ Add Item", style="Success.TButton",
                   command=self._inv_add).grid(row=0, column=0, sticky="ew", padx=4)
        ttk.Button(btn_row, text="✏️ Update Selected", style="Primary.TButton",
                   command=self._inv_update).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(btn_row, text="🗑 Delete Selected", style="Danger.TButton",
                   command=self._inv_delete).grid(row=0, column=2, sticky="ew", padx=4)

        cols = ("name","price","stock","category","barcode")
        self.tree_inv = ttk.Treeview(p, columns=cols, show="headings")
        for col, hdr, w in [("name","Item Name",200),("price","Price (₹)",90),
                             ("stock","Stock",80),("category","Category",120),
                             ("barcode","Barcode",160)]:
            self.tree_inv.heading(col, text=hdr)
            self.tree_inv.column(col, width=w, anchor="center")
        self.tree_inv.tag_configure("low", foreground="#c62828")
        self.tree_inv.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0,8))
        self.tree_inv.bind("<<TreeviewSelect>>", self._inv_select)
        sb2 = ttk.Scrollbar(p, command=self.tree_inv.yview)
        sb2.grid(row=1, column=1, sticky="ns", pady=(0,8))
        self.tree_inv.configure(yscrollcommand=sb2.set)

    # ════════════════════════════════════════
    #  TAB 4 — BILL HISTORY
    # ════════════════════════════════════════
    def _build_history_tab(self):
        p = self.tab_history
        p.rowconfigure(1, weight=1)
        p.columnconfigure(0, weight=1)

        ctrl = ttk.Frame(p)
        ctrl.grid(row=0, column=0, sticky="ew", padx=8, pady=8)

        ttk.Label(ctrl, text="Show last:").pack(side="left")
        self.cmb_days = ttk.Combobox(ctrl, values=["7","30","90","365"],
                                     width=5, state="readonly")
        self.cmb_days.current(1)
        self.cmb_days.pack(side="left", padx=4)
        ttk.Label(ctrl, text="days").pack(side="left")
        ttk.Button(ctrl, text="🔍 Refresh", style="Primary.TButton",
                   command=self._load_history).pack(side="left", padx=8)

        # ── Phone search ──────────────────────────────────────────────────────
        ttk.Separator(ctrl, orient="vertical").pack(side="left", fill="y",
                                                    padx=10, pady=4)
        tk.Label(ctrl, text="📱 Search by Phone:",
                 font=("Segoe UI", 9, "bold")).pack(side="left")
        self.ent_hist_phone = ttk.Entry(ctrl, width=16)
        self.ent_hist_phone.pack(side="left", padx=4)
        self.ent_hist_phone.bind("<Return>", self._search_bills_by_phone)
        ttk.Button(ctrl, text="Search", style="Warn.TButton",
                   command=self._search_bills_by_phone).pack(side="left", padx=2)
        ttk.Button(ctrl, text="✖ Clear", style="Danger.TButton",
                   command=self._clear_phone_search).pack(side="left", padx=2)
        # ──────────────────────────────────────────────────────────────────────

        ttk.Button(ctrl, text="📄 View Bill", style="Warn.TButton",
                   command=self._view_bill_detail).pack(side="left", padx=8)
        ttk.Button(ctrl, text="📤 Re-export PDF", style="Success.TButton",
                   command=self._reexport_pdf).pack(side="left", padx=4)

        cols = ("bill_no","customer","phone","total","payment","date")
        self.tree_hist = ttk.Treeview(p, columns=cols, show="headings")
        for col, hdr, w in [("bill_no","Bill No",160),("customer","Customer",140),
                             ("phone","Phone",110),("total","Total (₹)",100),
                             ("payment","Payment",80),("date","Date & Time",150)]:
            self.tree_hist.heading(col, text=hdr)
            self.tree_hist.column(col, width=w, anchor="center")
        self.tree_hist.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0,8))
        sb3 = ttk.Scrollbar(p, command=self.tree_hist.yview)
        sb3.grid(row=1, column=1, sticky="ns", pady=(0,8))
        self.tree_hist.configure(yscrollcommand=sb3.set)

    # ════════════════════════════════════════
    #  TAB 5 — REPORTS
    # ════════════════════════════════════════
    def _build_reports_tab(self):
        p = self.tab_reports
        p.columnconfigure((0,1), weight=1)
        p.rowconfigure(1, weight=1)

        ctrl = ttk.Frame(p)
        ctrl.grid(row=0, column=0, columnspan=2, sticky="ew", padx=8, pady=8)
        ttk.Label(ctrl, text="Period:").pack(side="left")
        self.cmb_rep_days = ttk.Combobox(ctrl, values=["7","30","90","365"],
                                         width=5, state="readonly")
        self.cmb_rep_days.current(1)
        self.cmb_rep_days.pack(side="left", padx=4)
        ttk.Label(ctrl, text="days").pack(side="left")
        ttk.Button(ctrl, text="📊 Load Report", style="Primary.TButton",
                   command=self._load_reports).pack(side="left", padx=8)

        self.rep_summary = ttk.LabelFrame(p, text=" Summary ", padding=10)
        self.rep_summary.grid(row=1, column=0, sticky="nsew", padx=(8,4), pady=4)

        top_f = ttk.LabelFrame(p, text=" Top 5 Items by Revenue ", padding=10)
        top_f.grid(row=1, column=1, sticky="nsew", padx=(4,8), pady=4)
        top_f.rowconfigure(0, weight=1)
        top_f.columnconfigure(0, weight=1)

        cols = ("item","qty","revenue")
        self.tree_top = ttk.Treeview(top_f, columns=cols, show="headings", height=8)
        for col, hdr, w in [("item","Item",180),("qty","Units Sold",100),
                             ("revenue","Revenue (₹)",120)]:
            self.tree_top.heading(col, text=hdr)
            self.tree_top.column(col, width=w, anchor="center")
        self.tree_top.grid(row=0, column=0, sticky="nsew")

    # ════════════════════════════════════════
    #  BILLING LOGIC
    # ════════════════════════════════════════
    def _refresh_bill_no(self):
        self._current_bill_no = self.db.next_bill_no()
        self.lbl_bill_no.configure(text=f"Bill No: {self._current_bill_no}")

    def _refresh_item_list(self):
        self.cmb_item["values"] = self.db.get_item_names()

    def _lookup_customer_on_phone(self, event=None):
        """Auto-fill customer name and loyalty points when phone is entered."""
        phone = self.ent_phone.get().strip()
        if not phone:
            return
        cust = self.db.get_customer_by_phone(phone)
        if cust:
            # cust columns: id,name,phone,email,address,loyalty_points,total_spent,joined_on
            self.ent_customer.delete(0, "end")
            self.ent_customer.insert(0, cust[1])
            pts = cust[5]
            worth = pts * LOYALTY_VALUE
            self.lbl_loyalty.configure(
                text=f"{pts} points available  (worth ₹{worth:.2f})",
                fg=self.COLORS["warn"])
        else:
            self.lbl_loyalty.configure(text="New customer — will be registered on bill save",
                                       fg="#888")
        self._update_bill_preview()

    def _apply_redeem(self):
        phone = self.ent_phone.get().strip()
        if not phone:
            messagebox.showwarning("No Customer", "Enter customer phone first."); return
        cust = self.db.get_customer_by_phone(phone)
        if not cust:
            messagebox.showwarning("Unknown", "Customer not found. Save the bill first."); return
        try:
            pts = int(self.ent_redeem.get() or 0)
            if pts < 0: raise ValueError
        except ValueError:
            messagebox.showerror("Error", "Enter a valid number of points."); return
        if pts > cust[5]:
            messagebox.showerror("Insufficient",
                f"Only {cust[5]} points available."); return
        self._redeem_points = pts
        worth = pts * LOYALTY_VALUE
        self.lbl_redeem_status.configure(
            text=f"✅  ₹{worth:.2f} will be deducted",
            fg=self.COLORS["success"])
        self._update_bill_preview()

    def _calc_totals(self):
        subtotal = sum(a for _,_,_,a in self.cart)
        try:
            dp = float(self.ent_discount.get() or 0)
            dp = max(0, min(100, dp))
        except: dp = 0
        disc      = subtotal * dp / 100
        gst       = subtotal * GST_RATE
        redeem_val = self._redeem_points * LOYALTY_VALUE
        total     = subtotal + gst - disc - redeem_val
        total     = max(0, total)
        points_earned = int(subtotal // LOYALTY_EARN_PER)
        return subtotal, dp, disc, gst, total, points_earned

    def _autofill_price(self, event=None):
        name  = self.cmb_item.get()
        price = self.db.get_item_price(name)
        stock = self.db.get_item_stock(name)
        if price:
            self.ent_price.delete(0, "end")
            self.ent_price.insert(0, str(price))
        color = "#c62828" if stock < 10 else "#2e7d32"
        self.lbl_stock.configure(text=f"Stock: {stock}", foreground=color)

    def _filter_items(self, event=None):
        q = self.cmb_item.get().lower()
        self.cmb_item["values"] = [i for i in self.db.get_item_names()
                                   if q in i.lower()]

    def _add_item(self):
        try:
            item  = self.cmb_item.get().strip()
            qty   = int(self.ent_qty.get())
            price = float(self.ent_price.get())
            if not item: raise ValueError("Item name empty")
            if qty <= 0 or price < 0: raise ValueError("Invalid qty/price")

            stock = self.db.get_item_stock(item)
            if stock > 0 and qty > stock:
                if not messagebox.askyesno("Low Stock",
                    f"Only {stock} units in stock. Add anyway?"): return

            amount = qty * price
            self.cart.append((item, qty, price, amount))
            self.tree_cart.insert("", "end",
                values=(item, qty, f"₹{price:.2f}", f"₹{amount:.2f}"))
            self.cmb_item.set(""); self.ent_qty.delete(0,"end")
            self.ent_price.delete(0,"end"); self.lbl_stock.configure(text="Stock: —")
            self._update_bill_preview()
            self.cmb_item.focus()
        except ValueError as ex:
            messagebox.showerror("Error", f"Invalid input: {ex}")

    def _remove_item(self):
        sel = self.tree_cart.selection()
        if not sel:
            messagebox.showinfo("Info", "Select an item to remove."); return
        idx = self.tree_cart.index(sel[0])
        self.cart.pop(idx)
        self.tree_cart.delete(sel[0])
        self._update_bill_preview()

    def _update_bill_preview(self):
        subtotal, dp, disc, gst, total, pts_earned = self._calc_totals()
        redeem_val = self._redeem_points * LOYALTY_VALUE
        now = datetime.datetime.now().strftime("%d-%m-%Y %H:%M")
        lines = [
            "=" * 44,
            f"  {SHOP_NAME}",
            f"  {SHOP_ADDRESS}",
            f"  Ph: {SHOP_PHONE}",
            "=" * 44,
            f"  Bill No : {self._current_bill_no}",
            f"  Date    : {now}",
            f"  Customer: {self.ent_customer.get() or '—'}",
            f"  Phone   : {self.ent_phone.get() or '—'}",
            "-" * 44,
            f"{'ITEM':<18}{'QTY':>4} {'PRICE':>9} {'AMT':>9}",
            "-" * 44,
        ]
        for item, qty, price, amount in self.cart:
            lines.append(
                f"{item[:18]:<18}{qty:>4} {'₹'+f'{price:.2f}':>9} {'₹'+f'{amount:.2f}':>9}")
        lines += [
            "-" * 44,
            f"{'Subtotal:':>30} {'₹'+f'{subtotal:.2f}':>9}",
            f"{'Discount ('+str(dp)+'%):':>30} {'-₹'+f'{disc:.2f}':>9}",
            f"{'GST (5%):':>30} {'₹'+f'{gst:.2f}':>9}",
        ]
        if self._redeem_points:
            lines.append(
                f"{'Points Redeemed ('+str(self._redeem_points)+'pts):':>30} {'-₹'+f'{redeem_val:.2f}':>9}")
        lines += [
            "=" * 44,
            f"{'TOTAL:':>30} {'₹'+f'{total:.2f}':>9}",
            f"{'Payment:':>30} {self.cmb_payment.get():>9}",
            "=" * 44,
        ]
        if pts_earned:
            lines.append(f"  🎁 Points earned this bill: {pts_earned}")
        lines.append("    Thank you! Visit again 🙏")
        lines.append("=" * 44)

        self.txt_bill.configure(state="normal")
        self.txt_bill.delete("1.0", "end")
        self.txt_bill.insert("end", "\n".join(lines))
        self.txt_bill.configure(state="disabled")

    def _generate_bill(self):
        if not self.cart:
            messagebox.showwarning("Empty Cart", "Add items before generating bill."); return
        subtotal, dp, disc, gst, total, pts_earned = self._calc_totals()
        phone    = self.ent_phone.get().strip()
        cust_name = self.ent_customer.get().strip()

        # Auto-register customer if phone + name given
        if phone and cust_name:
            self.db.upsert_customer(cust_name, phone)

        # Validate redemption
        if self._redeem_points and phone:
            if not self.db.redeem_points(phone, self._redeem_points):
                messagebox.showerror("Redemption Failed",
                    "Insufficient points. Clear redemption and retry."); return

        self.db.save_bill(
            self._current_bill_no, cust_name, phone,
            self.cart, subtotal, dp, disc, gst, total,
            self.cmb_payment.get(), pts_earned, self._redeem_points)
        self.bill_generated = True

        # Refresh loyalty display
        if phone:
            cust = self.db.get_customer_by_phone(phone)
            if cust:
                pts = cust[5]
                self.lbl_loyalty.configure(
                    text=f"{pts} points available  (worth ₹{pts*LOYALTY_VALUE:.2f})",
                    fg=self.COLORS["warn"])

        messagebox.showinfo("✅ Success",
            f"Bill {self._current_bill_no} saved!\n"
            f"Total: ₹{total:.2f}\n"
            f"Points Earned: {pts_earned}"
            + (f"\nPoints Redeemed: {self._redeem_points}" if self._redeem_points else ""))

    def _export_pdf(self):
        if not self.cart:
            messagebox.showwarning("Empty", "No items to export."); return
        subtotal, dp, disc, gst, total, pts_earned = self._calc_totals()
        phone = self.ent_phone.get().strip()
        loyalty_pts = None
        if phone:
            cust = self.db.get_customer_by_phone(phone)
            if cust: loyalty_pts = cust[5]

        path = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            initialfile=f"{self._current_bill_no}.pdf",
            filetypes=[("PDF Files","*.pdf")])
        if not path: return
        PDFGenerator.generate(
            self._current_bill_no,
            self.ent_customer.get(), phone,
            self.cart, subtotal, dp, disc, gst, total,
            self.cmb_payment.get(), path,
            pts_earned, self._redeem_points, loyalty_pts)
        messagebox.showinfo("PDF Saved", f"Saved to:\n{path}")

    def _send_whatsapp(self):
        phone = self.ent_phone.get().strip().replace(" ","").replace("+","")
        if not phone:
            messagebox.showwarning("No Phone", "Enter customer phone number."); return
        subtotal, dp, disc, gst, total, pts_earned = self._calc_totals()
        redeem_val = self._redeem_points * LOYALTY_VALUE
        msg = (f"*{SHOP_NAME}*\n"
               f"Bill No: {self._current_bill_no}\n"
               f"Date: {datetime.datetime.now().strftime('%d-%m-%Y')}\n"
               f"Items: {len(self.cart)}\n"
               f"Subtotal: ₹{subtotal:.2f}\n"
               f"Discount: ₹{disc:.2f}\n"
               f"GST (5%): ₹{gst:.2f}\n")
        if self._redeem_points:
            msg += f"Points Redeemed: {self._redeem_points} (₹{redeem_val:.2f})\n"
        msg += (f"*TOTAL: ₹{total:.2f}*\n"
                f"Payment: {self.cmb_payment.get()}\n"
                f"🎁 Points earned: {pts_earned}\n"
                f"Thank you! Visit again 🙏")
        webbrowser.open(f"https://wa.me/{phone}?text={urllib.parse.quote(msg)}")

    def _new_bill(self):
        if self.cart and not self.bill_generated:
            if not messagebox.askyesno("Unsaved Bill",
                "Bill not saved. Start new bill?"): return
        self.cart.clear()
        self.bill_generated = False
        self._redeem_points = 0
        for row in self.tree_cart.get_children():
            self.tree_cart.delete(row)
        self.ent_customer.delete(0,"end")
        self.ent_phone.delete(0,"end")
        self.ent_discount.delete(0,"end"); self.ent_discount.insert(0,"0")
        self.ent_redeem.delete(0,"end");   self.ent_redeem.insert(0,"0")
        self.cmb_payment.current(0)
        self.lbl_loyalty.configure(text="— points available", fg="#e65100")
        self.lbl_redeem_status.configure(text="")
        self._refresh_bill_no()
        self.lbl_date.configure(
            text=datetime.datetime.now().strftime("Date: %d-%m-%Y  %H:%M"))
        self._update_bill_preview()
        self.ent_barcode.focus()

    # ── Barcode ──
    def _on_barcode_enter(self, event=None):
        barcode = self.ent_barcode.get().strip()
        if barcode: self._process_barcode(barcode)

    def _on_barcode_keyrelease(self, event=None):
        if self._scan_timer: self.after_cancel(self._scan_timer)
        self._scan_timer = self.after(200, self._scan_timer_fired)

    def _scan_timer_fired(self):
        barcode = self.ent_barcode.get().strip()
        if 6 <= len(barcode) <= 20:
            self._process_barcode(barcode)

    def _lookup_barcode_manual(self):
        barcode = self.ent_barcode.get().strip()
        if not barcode:
            messagebox.showinfo("Barcode", "Enter or scan a barcode first."); return
        self._process_barcode(barcode)

    def _process_barcode(self, barcode):
        result = self.db.lookup_by_barcode(barcode)
        if result:
            name, price, stock = result
            self.cmb_item.set(name)
            self.ent_price.delete(0,"end"); self.ent_price.insert(0, str(price))
            color = "#c62828" if stock < 10 else "#2e7d32"
            self.lbl_stock.configure(text=f"Stock: {stock}", foreground=color)
            self.ent_qty.delete(0,"end"); self.ent_qty.focus()
            self.lbl_scan_status.configure(
                text=f"✅  {name}  |  ₹{price:.2f}", fg=self.COLORS["success"])
            self.ent_barcode.delete(0,"end")
            self.ent_qty.bind("<Return>", lambda e: self._add_item_from_scan())
        else:
            self.lbl_scan_status.configure(
                text=f"❌  Barcode '{barcode}' not found",
                fg=self.COLORS["danger"])
            self.ent_barcode.select_range(0,"end"); self.ent_barcode.focus()
        self.after(4000, lambda: self.lbl_scan_status.configure(text=""))

    def _add_item_from_scan(self):
        self._add_item()
        self.ent_qty.unbind("<Return>")
        self.ent_barcode.focus()

    # ════════════════════════════════════════
    #  CUSTOMER LOGIC
    # ════════════════════════════════════════
    def _load_customers(self):
        self.ent_cust_search.delete(0,"end")
        self._populate_customer_tree(self.db.get_all_customers())

    def _search_customers(self, event=None):
        q = self.ent_cust_search.get().strip()
        if q:
            self._populate_customer_tree(self.db.search_customers(q))
        else:
            self._populate_customer_tree(self.db.get_all_customers())

    def _populate_customer_tree(self, rows):
        for r in self.tree_cust.get_children():
            self.tree_cust.delete(r)
        for name, phone, email, pts, spent, joined in rows:
            tag = "gold" if spent >= 5000 else ("loyal" if pts >= 20 else "")
            self.tree_cust.insert("", "end",
                values=(name, phone, email or "—", pts,
                        f"₹{spent:.2f}", joined), tags=(tag,))

    def _cust_select(self, event=None):
        sel = self.tree_cust.selection()
        if not sel: return
        vals = self.tree_cust.item(sel[0])["values"]
        for key, val in zip(
                ("cust_name","cust_phone","cust_email"),
                (vals[0], vals[1], vals[2] if vals[2] != "—" else "")):
            self._cust_vars[key].delete(0,"end")
            self._cust_vars[key].insert(0, val)

    def _cust_add(self):
        name  = self._cust_vars["cust_name"].get().strip()
        phone = self._cust_vars["cust_phone"].get().strip()
        email = self._cust_vars["cust_email"].get().strip()
        addr  = self._cust_vars["cust_address"].get().strip()
        if not name or not phone:
            messagebox.showerror("Error", "Name and Phone are required."); return
        if self.db.get_customer_by_phone(phone):
            messagebox.showerror("Duplicate", f"Phone {phone} already registered."); return
        self.db.upsert_customer(name, phone, email, addr)
        self._load_customers()
        messagebox.showinfo("Added", f"Customer '{name}' added.")

    def _cust_update(self):
        sel = self.tree_cust.selection()
        if not sel:
            messagebox.showinfo("Select", "Select a customer to update."); return
        old_phone = self.tree_cust.item(sel[0])["values"][1]
        name  = self._cust_vars["cust_name"].get().strip()
        phone = self._cust_vars["cust_phone"].get().strip()
        email = self._cust_vars["cust_email"].get().strip()
        addr  = self._cust_vars["cust_address"].get().strip()
        if not name or not phone:
            messagebox.showerror("Error", "Name and Phone are required."); return
        self.db.update_customer_details(old_phone, name, phone, email, addr)
        self._load_customers()
        messagebox.showinfo("Updated", f"Customer updated.")

    def _cust_delete(self):
        sel = self.tree_cust.selection()
        if not sel:
            messagebox.showinfo("Select", "Select a customer to delete."); return
        name  = self.tree_cust.item(sel[0])["values"][0]
        phone = self.tree_cust.item(sel[0])["values"][1]
        if messagebox.askyesno("Confirm",
                f"Delete customer '{name}' ({phone})?\nBill records are kept."):
            self.db.delete_customer(phone)
            self._load_customers()

    def _view_customer_history(self):
        sel = self.tree_cust.selection()
        if not sel:
            messagebox.showinfo("Select", "Select a customer first."); return
        vals  = self.tree_cust.item(sel[0])["values"]
        name, phone, _, pts, spent, joined = vals
        bills = self.db.get_customer_bills(phone)

        win = tk.Toplevel(self)
        win.title(f"Purchase History — {name} ({phone})")
        win.geometry("640x500")
        win.configure(bg=self.COLORS["bg"])

        # Header card
        card = tk.Frame(win, bg=self.COLORS["primary"], padx=12, pady=10)
        card.pack(fill="x", padx=10, pady=10)
        tk.Label(card, text=f"👤  {name}",
                 bg=self.COLORS["primary"], fg="white",
                 font=("Segoe UI", 13, "bold")).pack(anchor="w")
        tk.Label(card, text=f"📱 {phone}   |   🎁 {pts} pts   |   💰 Total Spent: ₹{spent}   |   📅 Since {joined}",
                 bg=self.COLORS["primary"], fg="#c5cae9",
                 font=("Segoe UI", 9)).pack(anchor="w")

        # Bills table
        cols = ("bill_no","total","payment","pts_earned","pts_redeemed","date")
        tree = ttk.Treeview(win, columns=cols, show="headings")
        for col, hdr, w in [("bill_no","Bill No",150),("total","Total (₹)",90),
                             ("payment","Payment",80),("pts_earned","Pts Earned",90),
                             ("pts_redeemed","Pts Redeemed",100),("date","Date",150)]:
            tree.heading(col, text=hdr)
            tree.column(col, width=w, anchor="center")
        tree.pack(fill="both", expand=True, padx=10, pady=(0,10))

        for b in bills:
            bill_no, cname, total, pay, pe, pr, dt = b
            tree.insert("", "end",
                values=(bill_no, f"₹{total:.2f}", pay, pe, pr, dt))

        if not bills:
            ttk.Label(win, text="No bills found for this customer.",
                      style="Title.TLabel").pack(pady=20)

    # ════════════════════════════════════════
    #  INVENTORY LOGIC
    # ════════════════════════════════════════
    def _load_inventory(self):
        for row in self.tree_inv.get_children():
            self.tree_inv.delete(row)
        for name, price, stock, cat, barcode in self.db.get_items():
            tag = "low" if stock < 10 else ""
            self.tree_inv.insert("", "end",
                values=(name, f"₹{price:.2f}", stock, cat, barcode or ""),
                tags=(tag,))

    def _inv_select(self, event=None):
        sel = self.tree_inv.selection()
        if not sel: return
        vals = self.tree_inv.item(sel[0])["values"]
        for key, val in zip(
                ("inv_name","inv_price","inv_stock","inv_category","inv_barcode"),
                (vals[0], str(vals[1]).replace("₹",""), str(vals[2]),
                 vals[3], vals[4] if len(vals) > 4 else "")):
            self._inv_vars[key].delete(0,"end")
            self._inv_vars[key].insert(0, val)

    def _inv_add(self):
        try:
            name    = self._inv_vars["inv_name"].get().strip()
            price   = float(self._inv_vars["inv_price"].get())
            stock   = int(self._inv_vars["inv_stock"].get())
            cat     = self._inv_vars["inv_category"].get().strip() or "General"
            barcode = self._inv_vars["inv_barcode"].get().strip()
            if not name: raise ValueError
            if not self.db.add_item(name, price, stock, cat, barcode):
                messagebox.showerror("Duplicate", f"'{name}' already exists."); return
            self._load_inventory(); self._refresh_item_list()
            messagebox.showinfo("Added", f"'{name}' added.")
        except: messagebox.showerror("Error", "Check all fields are valid.")

    def _inv_update(self):
        sel = self.tree_inv.selection()
        if not sel:
            messagebox.showinfo("Select", "Select an item to update."); return
        old_name = self.tree_inv.item(sel[0])["values"][0]
        try:
            name    = self._inv_vars["inv_name"].get().strip()
            price   = float(self._inv_vars["inv_price"].get())
            stock   = int(self._inv_vars["inv_stock"].get())
            cat     = self._inv_vars["inv_category"].get().strip() or "General"
            barcode = self._inv_vars["inv_barcode"].get().strip()
            self.db.update_item(old_name, name, price, stock, cat, barcode)
            self._load_inventory(); self._refresh_item_list()
            messagebox.showinfo("Updated", f"'{name}' updated.")
        except: messagebox.showerror("Error", "Check all fields are valid.")

    def _inv_delete(self):
        sel = self.tree_inv.selection()
        if not sel:
            messagebox.showinfo("Select", "Select an item to delete."); return
        name = self.tree_inv.item(sel[0])["values"][0]
        if messagebox.askyesno("Confirm", f"Delete '{name}'?"):
            self.db.delete_item(name)
            self._load_inventory(); self._refresh_item_list()

    # ════════════════════════════════════════
    #  HISTORY LOGIC
    # ════════════════════════════════════════
    def _load_history(self):
        self.ent_hist_phone.delete(0,"end")
        self._populate_hist_tree(self.db.get_bills(int(self.cmb_days.get())))

    def _search_bills_by_phone(self, event=None):
        phone = self.ent_hist_phone.get().strip()
        if not phone:
            messagebox.showinfo("Search", "Enter a phone number to search."); return
        results = self.db.search_bills_by_phone(phone)
        self._populate_hist_tree(results)
        if not results:
            messagebox.showinfo("No Results", f"No bills found for phone: {phone}")

    def _clear_phone_search(self):
        self.ent_hist_phone.delete(0,"end")
        self._load_history()

    def _populate_hist_tree(self, rows):
        for r in self.tree_hist.get_children():
            self.tree_hist.delete(r)
        for b in rows:
            self.tree_hist.insert("", "end",
                values=(b[0], b[1] or "Walk-in", b[2] or "—",
                        f"₹{b[3]:.2f}", b[4], b[5]))

    def _view_bill_detail(self):
        sel = self.tree_hist.selection()
        if not sel:
            messagebox.showinfo("Select", "Select a bill to view."); return
        bill_no = self.tree_hist.item(sel[0])["values"][0]
        h  = self.db.get_bill_header(bill_no)
        it = self.db.get_bill_items(bill_no)
        # h columns: id,bill_no,customer,phone,subtotal,disc_pct,disc_amt,
        #            gst_amt,total,payment,points_earned,points_redeemed,created_at
        lines = [
            f"{'='*44}",
            f"  {SHOP_NAME}",
            f"  Bill No : {h[1]}",
            f"  Date    : {h[12]}",
            f"  Customer: {h[2] or '—'}",
            f"  Phone   : {h[3] or '—'}",
            f"{'-'*44}",
        ]
        for item, qty, price, amount in it:
            lines.append(f"  {item[:20]:<20} x{qty}  ₹{price:.2f}  → ₹{amount:.2f}")
        lines += [
            f"{'-'*44}",
            f"  Subtotal  : ₹{h[4]:.2f}",
            f"  Discount  : ₹{h[6]:.2f} ({h[5]}%)",
            f"  GST (5%)  : ₹{h[7]:.2f}",
        ]
        if h[11]:  # points_redeemed
            lines.append(f"  Pts Redeem: {h[11]} pts  (-₹{h[11]*LOYALTY_VALUE:.2f})")
        lines += [
            f"  TOTAL     : ₹{h[8]:.2f}",
            f"  Payment   : {h[9]}",
            f"  Pts Earned: {h[10] or 0}",
            f"{'='*44}",
        ]
        win = tk.Toplevel(self)
        win.title(f"Bill Detail — {bill_no}")
        win.geometry("480x520")
        txt = tk.Text(win, font=("Consolas",10), bg=self.COLORS["bill_bg"])
        txt.pack(fill="both", expand=True, padx=8, pady=8)
        txt.insert("end", "\n".join(lines))
        txt.configure(state="disabled")

    def _reexport_pdf(self):
        sel = self.tree_hist.selection()
        if not sel:
            messagebox.showinfo("Select", "Select a bill to export."); return
        bill_no = self.tree_hist.item(sel[0])["values"][0]
        h  = self.db.get_bill_header(bill_no)
        it = self.db.get_bill_items(bill_no)
        path = filedialog.asksaveasfilename(
            defaultextension=".pdf", initialfile=f"{bill_no}.pdf",
            filetypes=[("PDF Files","*.pdf")])
        if not path: return
        PDFGenerator.generate(bill_no, h[2], h[3], it,
                              h[4], h[5], h[6], h[7], h[8],
                              h[9], path, h[10] or 0, h[11] or 0)
        messagebox.showinfo("PDF Saved", f"Saved to:\n{path}")

    # ════════════════════════════════════════
    #  REPORTS LOGIC
    # ════════════════════════════════════════
    def _load_reports(self):
        days = int(self.cmb_rep_days.get())
        totals, top_items, pay_split = self.db.sales_summary(days)

        for w in self.rep_summary.winfo_children():
            w.destroy()

        bills_cnt  = totals[0] or 0
        revenue    = totals[1] or 0
        gst_total  = totals[2] or 0
        disc_total = totals[3] or 0

        cards = [
            ("Total Bills",     str(bills_cnt),       "#1a237e"),
            ("Total Revenue",   f"₹{revenue:.2f}",    "#2e7d32"),
            ("GST Collected",   f"₹{gst_total:.2f}",  "#e65100"),
            ("Total Discounts", f"₹{disc_total:.2f}", "#c62828"),
        ]
        for i, (lbl, val, col) in enumerate(cards):
            f = tk.Frame(self.rep_summary, bg=col, bd=0, relief="flat",
                         padx=16, pady=12)
            f.grid(row=0, column=i, padx=6, pady=6, sticky="nsew")
            self.rep_summary.columnconfigure(i, weight=1)
            tk.Label(f, text=lbl, bg=col, fg="white",
                     font=("Segoe UI", 9)).pack()
            tk.Label(f, text=val, bg=col, fg="white",
                     font=("Segoe UI", 14, "bold")).pack()

        ttk.Label(self.rep_summary, text="Payment Breakdown:",
                  style="Title.TLabel").grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(10,4))
        for i, (method, cnt, amt) in enumerate(pay_split):
            ttk.Label(self.rep_summary,
                text=f"  {method}: {cnt} bills  →  ₹{amt:.2f}").grid(
                row=2+i, column=0, columnspan=4, sticky="w")

        for row in self.tree_top.get_children():
            self.tree_top.delete(row)
        for item, qty, rev in top_items:
            self.tree_top.insert("", "end",
                values=(item, qty, f"₹{rev:.2f}"))

    # ════════════════════════════════════════
    #  TAB CHANGE
    # ════════════════════════════════════════
    def _on_tab_change(self, event):
        tab = event.widget.tab("current", "text").strip()
        if "Customers" in tab:  self._load_customers()
        elif "Inventory" in tab: self._load_inventory()
        elif "History" in tab:   self._load_history()
        elif "Reports" in tab:   self._load_reports()


# ══════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════
if __name__ == "__main__":
    app = BillingApp()
    app.mainloop()