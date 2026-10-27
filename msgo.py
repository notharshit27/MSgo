"""MSgo - GST billing for shops. Run: python msgo.py (Python 3.8+, nothing else to install). Data is saved in shop.db next to the app."""
import sqlite3, os, sys, shutil, webbrowser, pathlib, csv, base64, datetime as dt, tkinter as tk
from contextlib import nullcontext
from tkinter import ttk, filedialog, messagebox as mb
from html import escape as E

BASE = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(sys.argv[0]))
RES = getattr(sys, '_MEIPASS', BASE)
db = sqlite3.connect(os.path.join(BASE, "shop.db"))
db.row_factory = sqlite3.Row
db.execute("PRAGMA foreign_keys=ON")
db.executescript("""
CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY, name TEXT UNIQUE, hsn TEXT, unit TEXT, gst REAL, sp REAL, pp REAL, stock REAL, min REAL, barcode TEXT);
CREATE TABLE IF NOT EXISTS parties(id INTEGER PRIMARY KEY, name TEXT UNIQUE, phone TEXT, gstin TEXT, addr TEXT);
CREATE TABLE IF NOT EXISTS bills(id INTEGER PRIMARY KEY, kind TEXT, no TEXT, date TEXT, party TEXT, gstin TEXT, addr TEXT, inter INT,
  sub REAL, cgst REAL, sgst REAL, igst REAL, tax REAL, rnd REAL, total REAL, paid REAL, mode TEXT, ref TEXT);
CREATE TABLE IF NOT EXISTS lines(id INTEGER PRIMARY KEY, bill_id INT, item_id INT, name TEXT, hsn TEXT, qty REAL, rate REAL, disc REAL, dtype TEXT DEFAULT '%', gst REAL, taxable REAL, tax REAL);
""")
db.executescript("""
CREATE TABLE IF NOT EXISTS payments(
    id INTEGER PRIMARY KEY, bill_id INTEGER NOT NULL REFERENCES bills(id), party_id INTEGER REFERENCES parties(id),
    date TEXT NOT NULL, amount REAL NOT NULL CHECK(amount > 0), mode TEXT NOT NULL, reference TEXT DEFAULT '',
    source TEXT NOT NULL DEFAULT 'manual', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS ledger_entries(
    id INTEGER PRIMARY KEY, party_id INTEGER NOT NULL REFERENCES parties(id), account TEXT NOT NULL,
    bill_id INTEGER REFERENCES bills(id),
    payment_id INTEGER REFERENCES payments(id), entry_date TEXT NOT NULL, voucher TEXT NOT NULL,
    description TEXT NOT NULL, debit REAL NOT NULL DEFAULT 0, credit REAL NOT NULL DEFAULT 0,
    source_type TEXT NOT NULL, source_id INTEGER NOT NULL, UNIQUE(source_type, source_id));
CREATE TABLE IF NOT EXISTS import_batches(
    id INTEGER PRIMARY KEY, filename TEXT NOT NULL, kind TEXT NOT NULL, imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    invoice_count INTEGER NOT NULL DEFAULT 0, line_count INTEGER NOT NULL DEFAULT 0, total REAL NOT NULL DEFAULT 0);
""")
DEF = dict(shop_name="Maa Shakti Auto Parts", addr="", phone="", gstin="22BVVPK7852B1ZE", state="Chhattisgarh", prefix="MSAP/",
           next_no="1", next_pno="1", heading="TAX INVOICE", terms="Goods once sold will not be taken back or exchanged.",
           footer="Thank you, visit again!", color="#14213d", paper="A4", upi="", bank="", show_hsn="1", show_disc="1", default_gst="18", logo_b64="", logo_mime="",
           fiscal_start="", wizard_done="0", cash_ledger="Cash", bank_ledger="Bank", expense_ledger="General Expense")
for _a in ("ALTER TABLE lines ADD COLUMN dtype TEXT DEFAULT '%'", "ALTER TABLE bills ADD COLUMN ref TEXT",
                     "ALTER TABLE items ADD COLUMN barcode TEXT",
                     "ALTER TABLE parties ADD COLUMN role TEXT NOT NULL DEFAULT 'both'",
                     "ALTER TABLE parties ADD COLUMN opening_balance REAL NOT NULL DEFAULT 0",
                     "ALTER TABLE parties ADD COLUMN opening_date TEXT"):
    try: db.execute(_a)
    except sqlite3.OperationalError: pass
for _item in db.execute("SELECT id FROM items WHERE barcode IS NULL OR barcode='' ").fetchall():
    db.execute("UPDATE items SET barcode=? WHERE id=?", (f"MS{_item['id']:08d}", _item["id"]))
db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_items_barcode ON items(barcode) WHERE barcode IS NOT NULL AND barcode<>''")
db.execute("CREATE INDEX IF NOT EXISTS idx_ledger_party_date ON ledger_entries(party_id, entry_date, id)")
db.execute("CREATE INDEX IF NOT EXISTS idx_payments_bill_date ON payments(bill_id, date, id)")
for _bill in db.execute("SELECT * FROM bills").fetchall():
    _party = db.execute("SELECT id FROM parties WHERE name=?", (_bill["party"],)).fetchone()
    if _party is None:
        _party_id = db.execute("INSERT INTO parties(name,role,gstin,addr) VALUES(?,?,?,?)",
                               (_bill["party"], "customer" if _bill["kind"] == "sale" else "supplier", _bill["gstin"], _bill["addr"])).lastrowid
    else:
        _party_id = _party["id"]
    _account = "AR" if _bill["kind"] == "sale" else "AP"
    db.execute("INSERT OR IGNORE INTO ledger_entries(party_id,account,bill_id,entry_date,voucher,description,debit,credit,source_type,source_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
               (_party_id, _account, _bill["id"], _bill["date"], _bill["no"], "Sales invoice" if _account == "AR" else "Purchase invoice",
                _bill["total"] if _account == "AR" else 0, _bill["total"] if _account == "AP" else 0, "invoice", _bill["id"]))
    if _bill["paid"] > 0 and not db.execute("SELECT 1 FROM payments WHERE bill_id=? AND source='legacy'", (_bill["id"],)).fetchone():
        _payment_id = db.execute("INSERT INTO payments(bill_id,party_id,date,amount,mode,reference,source) VALUES(?,?,?,?,?,?,?)",
                                 (_bill["id"], _party_id, _bill["date"], _bill["paid"], _bill["mode"] or "Cash", _bill["ref"] or "", "legacy")).lastrowid
        db.execute("INSERT OR IGNORE INTO ledger_entries(party_id,account,bill_id,payment_id,entry_date,voucher,description,debit,credit,source_type,source_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   (_party_id, _account, _bill["id"], _payment_id, _bill["date"], _bill["no"], "Opening payment recorded on invoice",
                    0 if _account == "AR" else _bill["paid"], _bill["paid"] if _account == "AR" else 0, "payment", _payment_id))
db.commit()
_had_settings = db.execute("SELECT COUNT(*) FROM settings").fetchone()[0] > 0
for k, v in DEF.items():
    if k == "wizard_done" and _had_settings: v = "1"
    db.execute("INSERT OR IGNORE INTO settings VALUES(?,?)", (k, v))
db.commit()

def q(sql, *a): return db.execute(sql, a).fetchall()
def run(sql, *a):
    c = db.execute(sql, a); db.commit(); return c
def cfg(): return {r["k"]: r["v"] for r in q("SELECT * FROM settings")}
def today(): return dt.date.today().isoformat()
def money(x): return f"{x:,.2f}"
def dtext(l): return f"{l['disc']:g}%" if l["dtype"] == "%" else f"Rs {l['disc']:g}"

CODE39 = {
    "0": "nnnwwnwnn", "1": "wnnwnnnnw", "2": "nnwwnnnnw", "3": "wnwwnnnnn", "4": "nnnwwnnnw",
    "5": "wnnwwnnnn", "6": "nnwwwnnnn", "7": "nnnwnnwnw", "8": "wnnwnnwnn", "9": "nnwwnnwnn",
    "A": "wnnnnwnnw", "B": "nnwnnwnnw", "C": "wnwnnwnnn", "D": "nnnnwwnnw", "E": "wnnnwwnnn",
    "F": "nnwnwwnnn", "G": "nnnnnwwnw", "H": "wnnnnwwnn", "I": "nnwnnwwnn", "J": "nnnnwwwnn",
    "K": "wnnnnnnww", "L": "nnwnnnnww", "M": "wnwnnnnwn", "N": "nnnnwnnww", "O": "wnnnwnnwn",
    "P": "nnwnwnnwn", "Q": "nnnnnnwww", "R": "wnnnnnwwn", "S": "nnwnnnwwn", "T": "nnnnwnwwn",
    "U": "wwnnnnnnw", "V": "nwwnnnnnw", "W": "wwwnnnnnn", "X": "nwnnwnnnw", "Y": "wwnnwnnnn",
    "Z": "nwwnwnnnn", "-": "nwnnnnwnw", ".": "wwnnnnwnn", " ": "nwwnnnwnn", "$": "nwnwnwnnn",
    "/": "nwnwnnnwn", "+": "nwnnnwnwn", "%": "nnnwnwnwn", "*": "nwnnwnwnn"
}

def code39_svg(value):
    value = value.strip().upper()
    if not value or any(char not in CODE39 or char == "*" for char in value):
        raise ValueError("Code 39 supports A-Z, 0-9, space, and - . $ / + %.")
    x = 0; bars = []
    for char in "*" + value + "*":
        for element_index, width_code in enumerate(CODE39[char]):
            width = 6 if width_code == "w" else 2
            if element_index % 2 == 0: bars.append(f'<rect x="{x}" y="0" width="{width}" height="48"/>')
            x += width
        x += 2
    return f'<svg viewBox="0 0 {x} 48" preserveAspectRatio="none" aria-label="Barcode {E(value)}">{"".join(bars)}</svg>'

def print_item_labels(item_id=None):
    rows = q("SELECT id,name,barcode,sp FROM items WHERE id=?", item_id) if item_id is not None else q("SELECT id,name,barcode,sp FROM items ORDER BY name")
    if not rows: raise ValueError("No items are available to print.")
    labels = []
    for item in rows:
        code = item["barcode"] or f"MS{item['id']:08d}"
        labels.append(f'<section class="label"><strong>{E(item["name"])}</strong>{code39_svg(code)}<small>{E(code)} &nbsp; Rs {money(item["sp"])}</small></section>')
    document = f"""<!doctype html><html><head><meta charset="utf-8"><title>Item barcode labels</title><style>
@page{{size:A4;margin:8mm}}*{{box-sizing:border-box}}body{{margin:0;font:10pt Arial,sans-serif;color:#111}}
.sheet{{display:grid;grid-template-columns:repeat(3,1fr);gap:3mm}}.label{{height:35mm;border:1px dashed #999;padding:3mm;display:flex;flex-direction:column;justify-content:space-between;overflow:hidden}}
.label strong{{font-size:11pt;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}.label svg{{width:100%;height:13mm;fill:#000}}
.label small{{font-size:8pt;text-align:center;letter-spacing:1px}}@media print{{.label{{break-inside:avoid}}}}
</style></head><body><main class="sheet">{''.join(labels)}</main><script>window.onload=function(){{window.print()}}</script></body></html>"""
    folder = os.path.join(BASE, "bills"); os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "item-barcodes.html")
    with open(path, "w", encoding="utf-8") as file: file.write(document)
    webbrowser.open(pathlib.Path(path).as_uri())

def words(n):
    n = int(n)
    a = "One Two Three Four Five Six Seven Eight Nine Ten Eleven Twelve Thirteen Fourteen Fifteen Sixteen Seventeen Eighteen Nineteen".split()
    b = "_ _ Twenty Thirty Forty Fifty Sixty Seventy Eighty Ninety".split()
    w = lambda x: a[x - 1] if 0 < x < 20 else (b[x // 10] + (" " + a[x % 10 - 1] if x % 10 else "") if x >= 20 else "")
    r = ""
    for div, nm in ((10**7, "Crore"), (10**5, "Lakh"), (1000, "Thousand")):
        if n // div % 100: r += w(n // div % 100) + " " + nm + " "
    if n // 100 % 10: r += a[n // 100 % 10 - 1] + " Hundred "
    if n % 100: r += w(n % 100) + " "
    return (r.strip() or "Zero") + " Rupees Only"

def totals(lines, inter):
    ls, sub, tax = [], 0.0, 0.0
    for l in lines:
        base = l["qty"] * l["rate"]; t = base - (base * l["disc"] / 100 if l.get("dt", "%") == "%" else l["disc"]); x = t * l["gst"] / 100
        sub += t; tax += x; ls.append({**l, "taxable": t, "tax": x})
    raw = sub + tax; total = round(raw)
    return dict(ls=ls, sub=sub, tax=tax, cgst=0 if inter else tax / 2, sgst=0 if inter else tax / 2,
                igst=tax if inter else 0, rnd=total - raw, total=total)

def save_bill(kind, party, gstin, addr, date, lines, inter, paid, mode, ref="", invoice_no=None, source="manual", atomic=False):
    t = totals(lines, inter); s = cfg(); key = "next_no" if kind == "sale" else "next_pno"
    try: date = dt.date.fromisoformat(date).isoformat()
    except ValueError: raise ValueError("Invoice date must use YYYY-MM-DD format.")
    existing_party = db.execute("SELECT name FROM parties WHERE name=? COLLATE NOCASE", (party,)).fetchone()
    if existing_party: party = existing_party["name"]
    paid = float(paid)
    if paid < 0 or paid > t["total"]: raise ValueError("Paid amount must be between zero and the invoice total.")
    no = invoice_no or (s["prefix"] if kind == "sale" else "P-") + str(int(s[key])).zfill(4)
    with (nullcontext() if atomic else db):
        if db.execute("SELECT 1 FROM bills WHERE kind=? AND no=?", (kind, no)).fetchone():
            raise ValueError(f"{kind.title()} invoice {no} already exists.")
        if ref and db.execute("SELECT 1 FROM bills WHERE ref=?", (ref,)).fetchone():
            raise ValueError(f"Transaction ID {ref} was already imported.")
        if invoice_no is None: db.execute("UPDATE settings SET v=? WHERE k=?", (str(int(s[key]) + 1), key))
        cur = db.execute("INSERT INTO bills(kind,no,date,party,gstin,addr,inter,sub,cgst,sgst,igst,tax,rnd,total,paid,mode,ref) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (kind, no, date, party, gstin, addr, inter, t["sub"], t["cgst"], t["sgst"], t["igst"], t["tax"], t["rnd"], t["total"], paid, mode, ref))
        bid = cur.lastrowid
        for l in t["ls"]:
            if l["id"] is None:
                ex = db.execute("SELECT id FROM items WHERE name=?", (l["name"],)).fetchone()
                if ex: l["id"] = ex["id"]
                else:
                    l["id"] = db.execute("INSERT INTO items(name,hsn,unit,gst,sp,pp,stock,min) VALUES(?,?,?,?,?,?,?,0)",
                        (l["name"], l["hsn"], "pcs", l["gst"], l["rate"] if kind == "sale" else 0, l["rate"] if kind == "purchase" else 0, l["qty"] if kind == "sale" else 0)).lastrowid
                    db.execute("UPDATE items SET barcode=? WHERE id=?", (f"MS{l['id']:08d}", l["id"]))
            db.execute("INSERT INTO lines(bill_id,item_id,name,hsn,qty,rate,disc,dtype,gst,taxable,tax) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (bid, l["id"], l["name"], l["hsn"], l["qty"], l["rate"], l["disc"], l.get("dt", "%"), l["gst"], l["taxable"], l["tax"]))
            if kind == "sale": db.execute("UPDATE items SET stock=stock-? WHERE id=?", (l["qty"], l["id"]))
            else: db.execute("UPDATE items SET stock=stock+?, pp=? WHERE id=?", (l["qty"], l["rate"], l["id"]))
        role = "customer" if kind == "sale" else "supplier"
        db.execute("INSERT OR IGNORE INTO parties(name,phone,gstin,addr,role) VALUES(?,?,?,?,?)", (party, "", gstin, addr, role))
        party_id = db.execute("SELECT id FROM parties WHERE name=?", (party,)).fetchone()["id"]
        account = "AR" if kind == "sale" else "AP"
        db.execute("INSERT INTO ledger_entries(party_id,account,bill_id,entry_date,voucher,description,debit,credit,source_type,source_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (party_id, account, bid, date, no, "Sales invoice" if kind == "sale" else "Purchase invoice",
                    t["total"] if kind == "sale" else 0, t["total"] if kind == "purchase" else 0, "invoice", bid))
        if paid:
            payment_id = db.execute("INSERT INTO payments(bill_id,party_id,date,amount,mode,reference,source) VALUES(?,?,?,?,?,?,?)",
                                    (bid, party_id, date, paid, mode, ref, source)).lastrowid
            db.execute("INSERT INTO ledger_entries(party_id,account,bill_id,payment_id,entry_date,voucher,description,debit,credit,source_type,source_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (party_id, account, bid, payment_id, date, no, "Receipt" if kind == "sale" else "Payment",
                        0 if kind == "sale" else paid, paid if kind == "sale" else 0, "payment", payment_id))
    return bid

def record_payment(bill_id, amount, date, mode, reference=""):
    amount = float(amount)
    if amount <= 0: raise ValueError("Payment amount must be greater than zero.")
    try: date = dt.date.fromisoformat(date).isoformat()
    except ValueError: raise ValueError("Payment date must use YYYY-MM-DD format.")
    with db:
        bill = db.execute("SELECT * FROM bills WHERE id=?", (bill_id,)).fetchone()
        if bill is None: raise ValueError("Invoice no longer exists.")
        balance = bill["total"] - bill["paid"]
        if amount > balance + 0.005: raise ValueError("Payment exceeds the invoice balance.")
        party_id = db.execute("SELECT id FROM parties WHERE name=?", (bill["party"],)).fetchone()["id"]
        account = "AR" if bill["kind"] == "sale" else "AP"
        cur = db.execute("INSERT INTO payments(bill_id,party_id,date,amount,mode,reference) VALUES(?,?,?,?,?,?)",
                         (bill_id, party_id, date, amount, mode, reference))
        payment_id = cur.lastrowid
        db.execute("UPDATE bills SET paid=paid+? WHERE id=?", (amount, bill_id))
        db.execute("INSERT INTO ledger_entries(party_id,account,bill_id,payment_id,entry_date,voucher,description,debit,credit,source_type,source_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   (party_id, account, bill_id, payment_id, date, bill["no"], "Receipt" if account == "AR" else "Payment",
                    0 if account == "AR" else amount, amount if account == "AR" else 0, "payment", payment_id))
    return payment_id

def delete_bill(bid):
    with db:
        b = db.execute("SELECT kind FROM bills WHERE id=?", (bid,)).fetchone()["kind"]
        for line in db.execute("SELECT * FROM lines WHERE bill_id=?", (bid,)).fetchall():
            db.execute("UPDATE items SET stock=stock+? WHERE id=?", (line["qty"] if b == "sale" else -line["qty"], line["item_id"]))
        db.execute("DELETE FROM ledger_entries WHERE bill_id=?", (bid,))
        db.execute("DELETE FROM payments WHERE bill_id=?", (bid,))
        db.execute("DELETE FROM lines WHERE bill_id=?", (bid,)); db.execute("DELETE FROM bills WHERE id=?", (bid,))

def print_bill(bid):
    """Builds a ready-to-print bill page and opens it; the print window appears by itself."""
    s = cfg(); b = q("SELECT * FROM bills WHERE id=?", bid)[0]; L = q("SELECT * FROM lines WHERE bill_id=?", bid)
    th = s["paper"] == "Thermal 80mm"; col = s["color"]; sale = b["kind"] == "sale"
    page = "80mm auto" if th else ("A5" if s["paper"] == "A5" else "A4")
    hsn, dsc = s["show_hsn"] == "1", s["show_disc"] == "1"
    if th:
        rows = "".join(f"<tr><td colspan=2>{E(l['name'])}</td></tr><tr><td>{l['qty']:g} x {l['rate']:.2f} +{l['gst']:g}%</td><td class=r>{l['taxable']+l['tax']:.2f}</td></tr>" for l in L)
        head = ""
    else:
        head = "<tr><th>#</th><th>Item</th>" + ("<th>HSN</th>" if hsn else "") + "<th class=r>Qty</th><th class=r>Rate</th>" + ("<th class=r>Disc</th>" if dsc else "") + "<th class=r>Taxable</th><th class=r>GST</th><th class=r>Amount</th></tr>"
        rows = "".join(f"<tr><td>{i+1}</td><td>{E(l['name'])}</td>" + (f"<td>{E(l['hsn'] or '')}</td>" if hsn else "") +
                       f"<td class=r>{l['qty']:g}</td><td class=r>{l['rate']:.2f}</td>" + (f"<td class=r>{dtext(l)}</td>" if dsc else "") +
                       f"<td class=r>{l['taxable']:.2f}</td><td class=r>{l['gst']:g}%</td><td class=r>{l['taxable']+l['tax']:.2f}</td></tr>" for i, l in enumerate(L))
    tx = f"<tr><td>IGST</td><td class=r>{b['igst']:.2f}</td></tr>" if b["inter"] else f"<tr><td>CGST</td><td class=r>{b['cgst']:.2f}</td></tr><tr><td>SGST</td><td class=r>{b['sgst']:.2f}</td></tr>"
    extra = ""
    if sale:
        extra = (f"<p>{('Bank: ' + E(s['bank']) + '<br>') if s['bank'] else ''}{('UPI: ' + E(s['upi']) + '<br>') if s['upi'] else ''}<small>{E(s['terms'])}</small></p>"
                 f"<p class=r style='margin-top:{8 if th else 30}px'>For {E(s['shop_name'])}<br><small>Authorised signatory</small></p><p style='text-align:center;color:{col}'>{E(s['footer'])}</p>")
    logo = f"<img src='data:{s['logo_mime']};base64,{s['logo_b64']}' style='height:{40 if th else 60}px;display:block;margin:0 auto 4px'>" if s.get("logo_b64") else ""
    doc = f"""<!DOCTYPE html><html><head><meta charset=utf-8><title>{E(b['no'])}</title><style>
@page{{size:{page};margin:{'3mm' if th else '10mm'}}}
body{{font:{11 if th else 13}px/1.4 Arial,sans-serif;color:#111;margin:0;{'width:74mm' if th else ''}}}
table{{width:100%;border-collapse:collapse}} th{{background:{col};color:#fff;padding:5px;text-align:left}}
td{{padding:4px 5px;border-bottom:1px solid #ccc}} .r{{text-align:right}} .tot{{width:{'100%' if th else '45%'};margin-left:auto}}
.hd{{{'text-align:center' if th else 'border-bottom:3px solid ' + col + ';padding-bottom:8px'}}} h1{{margin:0;font-size:{15 if th else 22}px;color:{col}}}
.mid{{display:flex;justify-content:space-between;margin:8px 0}}
</style></head><body>
<div class=hd>{logo}<h1>{E(s['shop_name'])}</h1>{E(s['addr'])} {('Ph: ' + E(s['phone'])) if s['phone'] else ''}<br>GSTIN: {E(s['gstin'])}</div>
<p style="text-align:center;font-weight:700;color:{col}">{E(s['heading']) if sale else 'PURCHASE ENTRY'}</p>
<div class=mid><div><b>{'Bill to' if sale else 'Supplier'}:</b> {E(b['party'])}<br>{E(b['addr'] or '')}{('<br>GSTIN: ' + E(b['gstin'])) if b['gstin'] else ''}</div><div class=r>No: <b>{E(b['no'])}</b><br>Date: {b['date']}</div></div>
<table>{head}{rows}</table>
<table class=tot><tr><td>Taxable</td><td class=r>{b['sub']:.2f}</td></tr>{tx}<tr><td>Round off</td><td class=r>{b['rnd']:.2f}</td></tr>
<tr style="font-weight:700;font-size:1.15em"><td>TOTAL</td><td class=r>Rs. {b['total']:.2f}</td></tr><tr><td>Paid ({E(b['mode'])})</td><td class=r>{b['paid']:.2f}</td></tr><tr><td>Balance</td><td class=r>{b['total']-b['paid']:.2f}</td></tr></table>
<p><b>{words(b['total'])}</b></p>{extra}
<script>window.onload=function(){{setTimeout(function(){{window.print()}},300)}}</script></body></html>"""
    folder = os.path.join(BASE, "bills"); os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, b["no"].replace("/", "-") + ".html")
    with open(path, "w", encoding="utf-8") as f: f.write(doc)
    webbrowser.open(pathlib.Path(path).as_uri())

# ---------------- amount helper ----------------
def solve(qty, rate, dtype, disc, taxable, amount, gst):
    """Works out what is missing: give Qty+Rate, or Qty+Taxable value, or Qty+Amount (rate is found), or Rate+Taxable/Amount (qty is found)."""
    f = lambda v: float(v) if str(v).strip() else None
    qty, rate, disc, taxable, amount = f(qty), f(rate), f(disc) or 0.0, f(taxable), f(amount)
    if dtype == "%" and disc >= 100: raise ValueError("Discount must be less than 100%.")
    if taxable is None and amount is not None: taxable = amount / (1 + gst / 100)
    if taxable is not None:
        base = taxable / (1 - disc / 100) if dtype == "%" else taxable + disc
        if qty: rate = round(base / qty, 4)
        elif rate: qty = round(base / rate, 4)
    if not qty or rate is None: raise ValueError("Give Qty and Rate, or Qty and Taxable value (or Amount).")
    return qty, rate, disc

def parse_invoice_csv(path, kind):
    party_col = "CustomerName" if kind == "sale" else "SupplierName"
    price_col = "UnitPrice" if kind == "sale" else "UnitCost"
    required = {"date", "invoiceno", party_col.lower(), "itemname", "quantity", price_col.lower(), "taxrate", "totalamount", "transactionid"}
    with open(path, newline="", encoding="utf-8-sig", errors="strict") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames: raise ValueError("CSV needs a header row.")
        normalize = lambda value: "".join(ch.lower() for ch in value if ch.isalnum())
        columns = {normalize(name): name for name in reader.fieldnames}
        missing = sorted(required - columns.keys())
        if missing: raise ValueError("Missing required CSV columns: " + ", ".join(missing))
        groups = {}; errors = []
        for line_no, row in enumerate(reader, 2):
            cell = lambda name: (row.get(columns[normalize(name)]) or "").strip()
            invoice_no, party, item = cell("InvoiceNo"), cell(party_col), cell("ItemName")
            transaction_id = cell("TransactionID") or invoice_no
            key = (transaction_id, party.casefold(), invoice_no)
            group = groups.setdefault(key, {"invoice_no": invoice_no, "transaction_id": transaction_id,
                "party": party, "date": "", "lines": [], "errors": [], "source_rows": []})
            group["source_rows"].append(line_no)
            try:
                if not invoice_no or not party or not item: raise ValueError("Invoice number, party, and item are required.")
                try: parsed_date = dt.date.fromisoformat(cell("Date")).isoformat()
                except ValueError: raise ValueError("Date must use YYYY-MM-DD format.")
                quantity, price, gst, stated = (float(cell(col)) for col in ("Quantity", price_col, "TaxRate", "TotalAmount"))
                if quantity <= 0 or price < 0 or not 0 <= gst <= 100 or stated < 0: raise ValueError("Quantity, price, tax rate, or total is outside the allowed range.")
                expected = quantity * price * (1 + gst / 100)
                if abs(expected - stated) > 0.02: raise ValueError(f"Line total should be {money(expected)}, not {money(stated)}.")
                if group["date"] and group["date"] != parsed_date: raise ValueError("All rows in an invoice must use the same date.")
                group["date"] = parsed_date
                existing = q("SELECT id FROM items WHERE name=? COLLATE NOCASE", item)
                group["lines"].append(dict(id=existing[0]["id"] if existing else None, name=item,
                    hsn="", qty=quantity, rate=price, disc=0, dt="%", gst=gst))
            except (ValueError, TypeError) as error:
                group["errors"].append(f"Row {line_no}: {error}")
                errors.append(f"Row {line_no}: {error}")
        transactions = {}; invoice_numbers = {}
        for group in groups.values():
            if not group["lines"] and not group["errors"]:
                group["errors"].append("Invoice has no item lines.")
            previous = transactions.get(group["transaction_id"])
            if previous is not None:
                message = "Transaction ID is used by more than one invoice in this file."
                group["errors"].append(message); previous["errors"].append(message)
            else: transactions[group["transaction_id"]] = group
            previous_invoice = invoice_numbers.get(group["invoice_no"].casefold())
            if previous_invoice is not None:
                message = "Invoice number is repeated in this file."
                group["errors"].append(message); previous_invoice["errors"].append(message)
            else: invoice_numbers[group["invoice_no"].casefold()] = group
        return list(groups.values()), [error for group in groups.values() for error in group["errors"]]

def import_invoice_batch(groups, kind, filename):
    if not groups: raise ValueError("There are no invoices to import.")
    errors = [error for group in groups for error in group["errors"]]
    if errors: raise ValueError("Fix CSV validation errors before importing: " + errors[0])
    totals_by_group = [totals(group["lines"], 0)["total"] for group in groups]
    with db:
        for group in groups:
            if db.execute("SELECT 1 FROM bills WHERE kind=? AND no=?", (kind, group["invoice_no"])).fetchone():
                raise ValueError(f"Invoice {group['invoice_no']} already exists; no invoices were imported.")
            if db.execute("SELECT 1 FROM bills WHERE ref=?", (group["transaction_id"],)).fetchone():
                raise ValueError(f"Transaction ID {group['transaction_id']} already exists; no invoices were imported.")
        for group in groups:
            save_bill(kind, group["party"], "", "", group["date"], group["lines"], 0, 0, "Credit",
                      group["transaction_id"], group["invoice_no"], "csv", atomic=True)
        db.execute("INSERT INTO import_batches(filename,kind,invoice_count,line_count,total) VALUES(?,?,?,?,?)",
                   (os.path.basename(filename), kind, len(groups), sum(len(g["lines"]) for g in groups), sum(totals_by_group)))
    return len(groups), sum(totals_by_group)

# ---------------- MSgo UI (dark, minimal) ----------------
BG, PN, P2, HV, TX, MU, AC, OK, BAD = "#0b0e14", "#141a23", "#1d2531", "#2a3545", "#e8edf5", "#8793a6", "#8b5cf6", "#34d399", "#f87171"
FN = "Segoe UI"; R = None

def toast(msg, bad=False):
    t = tk.Label(R, text=msg, bg=BAD if bad else OK, fg=BG, font=(FN, 11, "bold"), padx=18, pady=9)
    t.place(relx=.5, rely=.97, anchor="s"); t.lift(); R.after(3200 if bad else 2200, t.destroy)
def lab(p, t, fg=MU, size=10, bold=False): return tk.Label(p, text=t, bg=p["bg"], fg=fg, font=(FN, size, "bold" if bold else "normal"), anchor="w")
def box(p, **k): return tk.Frame(p, bg=PN, **k)
def btn(p, text, cmd, kind="ghost"):
    bg, hv = {"pri": (AC, "#a78bfa"), "ghost": (P2, HV), "bad": ("#3a1d22", "#5a2a31")}[kind]
    b = tk.Button(p, text=text, command=cmd, bg=bg, fg="#fff" if kind == "pri" else TX, activebackground=hv, activeforeground="#fff", relief="flat", bd=0, padx=16, pady=8, font=(FN, 10, "bold"), cursor="hand2")
    b.bind("<Enter>", lambda e: b.config(bg=hv)); b.bind("<Leave>", lambda e: b.config(bg=bg)); return b
def put(p, label, r, c, w=12, kind="e", vals=None):
    lab(p, label, MU, 9).grid(row=r * 2, column=c, sticky="w", padx=5, pady=(8, 0))
    x = {"e": lambda: ttk.Entry(p, width=w), "c": lambda: ttk.Combobox(p, width=w), "r": lambda: ttk.Combobox(p, width=w, values=vals, state="readonly")}[kind]()
    x.grid(row=r * 2 + 1, column=c, sticky="we", padx=5, pady=(0, 4)); return x
def setv(e, v): e.delete(0, "end"); e.insert(0, "" if v is None else v)
def suggest(widget, names):
    text = widget.get().strip().casefold()
    if not text:
        widget["values"] = names[:30]
        return
    matches = [name for name in names if text in name.casefold()]
    matches.sort(key=lambda name: (not name.casefold().startswith(text), name.casefold()))
    widget["values"] = matches[:30]
    if matches:
        widget.after_idle(lambda: widget.tk.call("ttk::combobox::Post", str(widget)))
def chk(p, text, var): return tk.Checkbutton(p, text=text, variable=var, bg=p["bg"], fg=TX, selectcolor=P2, activebackground=p["bg"], activeforeground=TX, font=(FN, 10))
def tview(p, cols, widths, h=10):
    f = tk.Frame(p, bg=PN); tv = ttk.Treeview(f, columns=cols, show="headings", height=h)
    for c, w in zip(cols, widths): tv.heading(c, text=c.upper()); tv.column(c, width=w, anchor="w")
    sb = ttk.Scrollbar(f, command=tv.yview); tv.configure(yscrollcommand=sb.set); tv.pack(side="left", fill="both", expand=True); sb.pack(side="right", fill="y")
    tv.tag_configure("low", foreground=BAD); return f, tv
def style():
    R.option_add("*TCombobox*Listbox.background", P2); R.option_add("*TCombobox*Listbox.foreground", TX); R.option_add("*TCombobox*Listbox.selectBackground", AC); R.option_add("*TCombobox*Listbox.font", (FN, 10))
    st = ttk.Style(); st.theme_use("clam"); st.configure(".", background=BG, foreground=TX, font=(FN, 10))
    for w in ("TEntry", "TCombobox"): st.configure(w, fieldbackground=P2, foreground=TX, insertcolor=TX, bordercolor=P2, lightcolor=P2, darkcolor=P2, padding=7)
    st.configure("TCombobox", background=P2, arrowcolor=MU); st.map("TCombobox", fieldbackground=[("readonly", P2)], foreground=[("readonly", TX)], selectbackground=[("readonly", P2)], selectforeground=[("readonly", TX)])
    st.map("TEntry", bordercolor=[("focus", AC)], lightcolor=[("focus", AC)], darkcolor=[("focus", AC)])
    st.configure("Treeview", background=PN, fieldbackground=PN, foreground=TX, rowheight=32, borderwidth=0, font=(FN, 10))
    st.configure("Treeview.Heading", background=P2, foreground=MU, relief="flat", font=(FN, 9, "bold")); st.map("Treeview", background=[("selected", AC)], foreground=[("selected", "#fff")])
    st.configure("Vertical.TScrollbar", background=P2, troughcolor=PN, arrowcolor=MU, bordercolor=PN)
def draw_logo(c, s=44):
    c.create_polygon(4, 12, 12, 4, s - 12, 4, s - 4, 12, s - 4, s - 12, s - 12, s - 4, 12, s - 4, 4, s - 12, fill=AC, smooth=True, outline="")
    c.create_text(s / 2, s / 2 + 1, text="M", fill="#fff", font=(FN, 17, "bold"))

class Page(tk.Frame):
    def __init__(s, app, kind=None):
        super().__init__(app.body, bg=BG); s.app = app; s.kind = kind
        hd = tk.Frame(s, bg=BG); hd.pack(fill="x", padx=26, pady=(22, 6)); lab(hd, s.title, TX, 20, True).pack(side="left"); s.build()
    def refresh(s): pass

class Dash(Page):
    title = "Dashboard"
    def build(s):
        qa = tk.Frame(s, bg=BG); qa.pack(fill="x", padx=26, pady=(0, 6))
        for t, i, k in (("+ New sale", 1, "pri"), ("+ Purchase", 2, "ghost"), ("+ Item", 3, "ghost"), ("CSV import", 7, "ghost")): btn(qa, t, lambda i=i: s.app.show(i), k).pack(side="left", padx=(0, 8))
        row = tk.Frame(s, bg=BG); row.pack(fill="x", padx=20); s.cards = []
        for i, (t, c) in enumerate((("TODAY SALES", AC), ("MONTH SALES", OK), ("RECEIVABLES", "#fbbf24"),
                        ("PAYABLES", "#60a5fa"), ("MONTH PURCHASES", "#fb7185"), ("LOW STOCK", BAD))):
            f = box(row); f.grid(row=0, column=i, sticky="nsew", padx=6, pady=4); row.columnconfigure(i, weight=1)
            lab(f, t, MU, 9, True).pack(anchor="w", padx=18, pady=(14, 0)); v = tk.Label(f, text="0", bg=PN, fg=c, font=(FN, 22, "bold"), anchor="w"); v.pack(anchor="w", padx=18, pady=(2, 14)); s.cards.append(v)
        mid = tk.Frame(s, bg=BG); mid.pack(fill="both", expand=True, padx=20, pady=6)
        s.cv = tk.Canvas(mid, bg=PN, highlightthickness=0, height=250); s.cv.pack(side="left", fill="both", expand=True, padx=6)
        right = box(mid, width=420); right.pack(side="left", fill="both", padx=6); right.pack_propagate(False)
        lab(right, "RECENT RECEIPTS", MU, 9, True).pack(anchor="w", padx=14, pady=(12, 4)); f, s.tv = tview(right, ("Date", "Voucher", "Party", "Amount"), (80, 95, 155, 90), 6); f.pack(fill="both", expand=True, padx=8, pady=(0, 6))
        s.low = lab(right, "", BAD, 10); s.low.config(justify="left", wraplength=390); s.low.pack(anchor="w", padx=14, pady=(0, 12))
    def count(s, i, target, kind, step=0):
        v = target * min(step, 12) / 12; s.cards[i].config(text=("Rs " + money(v)) if kind == "m" else f"{int(v)}")
        if step < 12: s.after(28, lambda: s.count(i, target, kind, step + 1))
    def chart(s, p=0.0):
        c = s.cv; c.delete("all"); w = max(c.winfo_width(), 420); h = 250
        c.create_text(18, 18, anchor="w", text="SALES, LAST 7 DAYS", fill=MU, font=(FN, 9, "bold")); bw = (w - 50) / 7; mx = max(s.vals + [1])
        for i, (d, v) in enumerate(zip(s.days, s.vals)):
            x = 25 + i * bw; bh = (h - 90) * v / mx * p
            c.create_rectangle(x + 10, h - 34 - bh, x + bw - 10, h - 34, fill=AC if d == today() else HV, width=0)
            c.create_text(x + bw / 2, h - 16, text=d[8:] + "/" + d[5:7], fill=MU, font=(FN, 8))
            if v and p >= 1: c.create_text(x + bw / 2, h - 44 - bh, text=f"{v:,.0f}", fill=TX, font=(FN, 8, "bold"))
        if p < 1: s.after(22, lambda: s.chart(min(1, p + .12)))
    def refresh(s):
        sm = lambda kind, w, *a: q(f"SELECT COALESCE(SUM(total),0) x FROM bills WHERE kind=? AND {w}", kind, *a)[0]["x"]
        low = q("SELECT name,stock,unit FROM items WHERE min>0 AND stock<=min ORDER BY stock LIMIT 6")
        receivable = q("SELECT COALESCE(SUM(total-paid),0) x FROM bills WHERE kind='sale'")[0]["x"] + q("SELECT COALESCE(SUM(opening_balance),0) x FROM parties WHERE role IN ('customer','both')")[0]["x"]
        payable = q("SELECT COALESCE(SUM(total-paid),0) x FROM bills WHERE kind='purchase'")[0]["x"] + q("SELECT COALESCE(SUM(opening_balance),0) x FROM parties WHERE role='supplier'")[0]["x"]
        values = ((sm("sale", "date=?", today()), "m"), (sm("sale", "date LIKE ?", today()[:7] + "%"), "m"),
                  (receivable, "m"), (payable, "m"), (sm("purchase", "date LIKE ?", today()[:7] + "%"), "m"),
                  (len(q("SELECT 1 FROM items WHERE min>0 AND stock<=min")), "n"))
        for i, (v, k) in enumerate(values): s.count(i, v, k)
        s.days = [(dt.date.today() - dt.timedelta(d)).isoformat() for d in range(6, -1, -1)]; s.vals = [sm("sale", "date=?", d) for d in s.days]; s.update_idletasks(); s.chart()
        s.tv.delete(*s.tv.get_children())
        for r in q("SELECT p.date,b.no,b.party,p.amount FROM payments p JOIN bills b ON b.id=p.bill_id WHERE b.kind='sale' ORDER BY p.id DESC LIMIT 6"):
            s.tv.insert("", "end", values=(r["date"], r["no"], r["party"], money(r["amount"])))
        s.low.config(text=("Running low:  " + ",  ".join(f"{r['name']} ({r['stock']:g})" for r in low)) if low else "Stock looks good.")

class Bill(Page):
    @property
    def title(s): return "New Sale" if s.kind == "sale" else "New Purchase"
    def build(s):
        s.lines = []; who = "Customer" if s.kind == "sale" else "Supplier"
        top = box(s); top.pack(fill="x", padx=26, pady=4)
        s.party = put(top, who + " (new names are saved automatically)", 0, 0, 30, "c"); s.gstin = put(top, "GSTIN (optional)", 0, 1, 18); s.addr = put(top, "Address", 0, 2, 28); s.date = put(top, "Date", 0, 3, 12); setv(s.date, today())
        s.inter = tk.IntVar(); chk(top, "Other state (IGST)", s.inter).grid(row=1, column=4, padx=10); top.columnconfigure(2, weight=1)
        s.party.bind("<<ComboboxSelected>>", s.fill_party); s.party.bind("<KeyRelease>", s.suggest_parties)
        scan = box(s); scan.pack(fill="x", padx=26, pady=(4, 0))
        lab(scan, "Barcode scanner", TX, 10, True).pack(side="left", padx=(10, 8), pady=8)
        s.barcode = ttk.Entry(scan, width=28); s.barcode.pack(side="left", padx=4, pady=6)
        s.barcode.bind("<Return>", s.scan_barcode)
        btn(scan, "Scan / add item", s.scan_barcode, "pri").pack(side="left", padx=6)
        lab(scan, "Scan a label or type its code, then press Enter.", MU, 9).pack(side="left", padx=8)
        mid = box(s); mid.pack(fill="x", padx=26, pady=4)
        s.item = put(mid, "Item (type a new name to create it)", 0, 0, 30, "c"); s.hsn = put(mid, "HSN", 0, 1, 9); s.qty = put(mid, "Qty", 0, 2, 6); s.rate = put(mid, "Rate", 0, 3, 9)
        s.dtype = put(mid, "Disc type", 0, 4, 5, "r", ["%", "Rs"]); s.dtype.set("%"); s.disc = put(mid, "Discount", 0, 5, 8); s.taxv = put(mid, "Taxable", 0, 6, 10); s.gst = put(mid, "GST %", 0, 7, 6); s.amt = put(mid, "Amount (with GST)", 0, 8, 12)
        mid.columnconfigure(0, weight=1); s.info = lab(mid, "Fill Qty plus Rate, or Taxable, or Amount. The rest is worked out.", AC, 10); s.info.grid(row=2, column=0, columnspan=8, sticky="w", padx=6, pady=(2, 10))
        btn(mid, "+ Add", s.add, "pri").grid(row=2, column=8, padx=6, pady=(2, 10))
        for w in (s.hsn, s.qty, s.rate, s.disc, s.taxv, s.gst, s.amt): w.bind("<KeyRelease>", s.preview)
        for w in (s.qty, s.rate, s.taxv, s.amt): w.bind("<Return>", lambda e: s.add())
        s.dtype.bind("<<ComboboxSelected>>", s.preview); s.item.bind("<<ComboboxSelected>>", s.fill_item); s.item.bind("<KeyRelease>", s.filt)
        f, s.tv = tview(s, ("Item", "HSN", "Qty", "Rate", "Discount", "Taxable", "GST", "Amount"), (260, 90, 70, 90, 90, 110, 70, 110), 7); f.pack(fill="both", expand=True, padx=26, pady=6)
        bot = tk.Frame(s, bg=BG); bot.pack(fill="x", padx=26, pady=(2, 16))
        btn(bot, "Remove line", s.remove).pack(side="left"); s.tot = tk.Label(bot, text="Rs 0.00", bg=BG, fg=TX, font=(FN, 22, "bold")); s.tot.pack(side="left", padx=24)
        btn(bot, "SAVE" + (" + PRINT" if s.kind == "sale" else ""), s.save, "pri").pack(side="right")
        s.mode = ttk.Combobox(bot, values=["Cash", "UPI", "Card", "Bank", "Credit"], width=7, state="readonly"); s.mode.set("Cash"); s.mode.pack(side="right", padx=8)
        btn(bot, "Full paid", s.full).pack(side="right"); s.paid = ttk.Entry(bot, width=10); s.paid.pack(side="right", padx=6); lab(bot, "Paid now").pack(side="right")
    def refresh(s):
        s.party["values"] = [r["name"] for r in q("SELECT name FROM parties ORDER BY name")]; s.filt()
    def suggest_parties(s, event=None):
        if event and event.keysym in ("Up", "Down", "Return", "Escape", "Tab"): return
        suggest(s.party, [r["name"] for r in q("SELECT name FROM parties ORDER BY name")])
    def filt(s, e=None):
        if e and e.keysym in ("Up", "Down", "Return", "Escape", "Tab"): return
        suggest(s.item, [r["name"] for r in q("SELECT name FROM items ORDER BY name")]); s.preview()
    def fill_party(s, e=None):
        r = q("SELECT * FROM parties WHERE name=?", s.party.get())
        if r: setv(s.gstin, r[0]["gstin"]); setv(s.addr, r[0]["addr"])
    def fill_item(s, e=None):
        r = q("SELECT * FROM items WHERE name=?", s.item.get())
        if r:
            it = r[0]; setv(s.hsn, it["hsn"]); setv(s.rate, it["sp"] if s.kind == "sale" else it["pp"]); setv(s.gst, f"{it['gst']:g}"); setv(s.qty, 1)
            for w in (s.disc, s.taxv, s.amt): setv(w, "")
            s.qty.focus(); s.qty.select_range(0, "end")
        s.preview()
    def calc_line(s):
        gst = float(s.gst.get().strip() or cfg()["default_gst"])
        qty, rate, disc = solve(s.qty.get(), s.rate.get(), s.dtype.get(), s.disc.get(), s.taxv.get(), s.amt.get(), gst); return qty, rate, disc, gst
    def preview(s, e=None):
        name = s.item.get().strip()
        if not name: return s.info.config(text="Fill Qty plus Rate, or Taxable, or Amount. The rest is worked out.")
        msg = "" if q("SELECT 1 FROM items WHERE name=?", name) else "NEW item, it will be created.   "
        try:
            qty, rate, disc, gst = s.calc_line(); t = totals([dict(qty=qty, rate=rate, disc=disc, dt=s.dtype.get(), gst=gst)], 0)["ls"][0]
            msg += f"Qty {qty:g}   Rate {rate:g}   Taxable {money(t['taxable'])}   GST {money(t['tax'])}   Amount {money(t['taxable'] + t['tax'])}"
        except ValueError: pass
        s.info.config(text=msg)
    def add(s):
        name = s.item.get().strip()
        if not name: return toast("Type or pick the item name.", True)
        try: qty, rate, disc, gst = s.calc_line()
        except ValueError as x: return toast(str(x) if str(x)[:3] in ("Giv", "Dis") else "Please type numbers only.", True)
        if qty <= 0 or rate < 0: return toast("Quantity must be more than 0.", True)
        r = q("SELECT * FROM items WHERE name=? COLLATE NOCASE", name); it = r[0] if r else None
        if it: name = it["name"]
        if it and s.kind == "sale" and qty > it["stock"] and not mb.askyesno("Low stock", f"Only {it['stock']:g} in stock. Sell anyway?"): return
        s.lines.append(dict(id=it["id"] if it else None, name=name, hsn=s.hsn.get().strip() or (it["hsn"] if it else ""), qty=qty, rate=rate, disc=disc, dt=s.dtype.get(), gst=gst))
        for w in (s.item, s.hsn, s.qty, s.rate, s.disc, s.taxv, s.gst, s.amt): setv(w, "")
        s.show(); s.item.focus(); toast("Added")
    def scan_barcode(s, event=None):
        code = s.barcode.get().strip().upper()
        if not code: return "break"
        items = q("SELECT * FROM items WHERE barcode=? COLLATE NOCASE", code)
        if not items:
            toast(f"No item is assigned barcode {code}.", True); s.barcode.focus_set(); s.barcode.select_range(0, "end"); return "break"
        setv(s.item, items[0]["name"]); s.fill_item(); s.add(); setv(s.barcode, ""); s.barcode.focus_set()
        return "break"
    def remove(s):
        for i in sorted((int(x) for x in s.tv.selection()), reverse=True): del s.lines[i]
        s.show()
    def full(s): setv(s.paid, s.t["total"])
    def show(s):
        s.tv.delete(*s.tv.get_children()); s.t = totals(s.lines, s.inter.get())
        for i, l in enumerate(s.t["ls"]): s.tv.insert("", "end", iid=str(i), values=(l["name"], l["hsn"], f"{l['qty']:g}", f"{l['rate']:.2f}", f"{l['disc']:g}{'%' if l['dt'] == '%' else ' Rs'}", money(l["taxable"]), f"{l['gst']:g}%", money(l["taxable"] + l["tax"])))
        s.tot.config(text="Rs " + money(s.t["total"]))
    def save(s):
        name = s.party.get().strip()
        if not name or not s.lines: return toast("Add the party name and at least one item.", True)
        try: paid = float(s.paid.get().strip() or 0)
        except ValueError: return toast("Paid amount must be a number.", True)
        try: bid = save_bill(s.kind, name, s.gstin.get().strip().upper(), s.addr.get().strip(), s.date.get().strip() or today(), s.lines, s.inter.get(), paid, s.mode.get())
        except (ValueError, sqlite3.Error) as error: return toast(str(error), True)
        s.lines = []; s.inter.set(0)
        for w in (s.party, s.gstin, s.addr, s.paid): setv(w, "")
        s.show(); s.refresh(); toast("Bill saved")
        if s.kind == "sale": print_bill(bid)

class FormPage(Page):
    def build(s):
        s.sel = None; s.e = {}; f = box(s); f.pack(fill="x", padx=26, pady=6)
        for i, item in enumerate(s.fields):
            k, l = item[:2]; kind = item[2] if len(item) > 2 else "e"; vals = item[3] if len(item) > 3 else None
            s.e[k] = put(f, l, i // 4, i % 4, 20, kind, vals)
        if isinstance(s.e.get("name"), ttk.Combobox):
            s.e["name"].bind("<KeyRelease>", s.suggest_names)
            s.e["name"].bind("<<ComboboxSelected>>", s.select_name)
        for c in range(4): f.columnconfigure(c, weight=1)
        bt = tk.Frame(f, bg=PN); bt.grid(row=20, column=0, columnspan=4, sticky="w", padx=5, pady=10)
        s.actions = bt
        btn(bt, "Save", s.save, "pri").pack(side="left", padx=4); btn(bt, "Clear", s.clear).pack(side="left", padx=4); btn(bt, "Delete selected", s.delete, "bad").pack(side="left", padx=4)
        lab(bt, "Search").pack(side="left", padx=(24, 6)); s.srch = ttk.Entry(bt, width=22); s.srch.pack(side="left"); s.srch.bind("<KeyRelease>", lambda e: s.refresh())
        fr, s.tv = tview(s, s.cols, s.widths, 12); fr.pack(fill="both", expand=True, padx=26, pady=6); s.tv.bind("<<TreeviewSelect>>", s.pick)
    def clear(s):
        s.sel = None
        for e in s.e.values(): setv(e, "")
    def pick(s, ev=None):
        if not s.tv.selection(): return
        s.sel = int(s.tv.selection()[0]); r = q(f"SELECT * FROM {s.tn} WHERE id=?", s.sel)[0]
        for k, e in s.e.items(): setv(e, r[k])
    def suggest_names(s, event=None):
        if event and event.keysym in ("Up", "Down", "Return", "Escape", "Tab"): return
        suggest(s.e["name"], [r["name"] for r in q(f"SELECT name FROM {s.tn} ORDER BY name")])
    def select_name(s, event=None):
        name = s.e["name"].get().strip()
        rows = q(f"SELECT * FROM {s.tn} WHERE name=? COLLATE NOCASE", name)
        if rows:
            s.sel = rows[0]["id"]
            for key, entry in s.e.items(): setv(entry, rows[0][key])
    def delete(s):
        if s.sel and mb.askyesno("Delete", "Delete the selected entry?"): run(f"DELETE FROM {s.tn} WHERE id=?", s.sel); s.clear(); s.refresh(); toast("Deleted")
    def save(s):
        d = {k: e.get().strip() for k, e in s.e.items()}
        if not d["name"]: return toast("Please type the name.", True)
        if "barcode" in d:
            d["barcode"] = d["barcode"].upper()
            if d["barcode"]:
                try: code39_svg(d["barcode"])
                except ValueError as error: return toast(str(error), True)
        for k in s.nums:
            try: d[k] = float(d[k] or 0)
            except ValueError: return toast("Numeric fields must contain valid numbers.", True)
        if "opening_date" in d and d["opening_date"]:
            try: d["opening_date"] = dt.date.fromisoformat(d["opening_date"]).isoformat()
            except ValueError: return toast("Opening date must use YYYY-MM-DD format.", True)
        if d.get("opening_balance", 0) < 0: return toast("Opening balance cannot be negative.", True)
        ex = None if s.sel else q(f"SELECT id,name FROM {s.tn} WHERE name=? COLLATE NOCASE", d["name"])
        if ex: return toast("This name already exists. Select it from suggestions to edit it.", True)
        sid = s.sel or (ex[0]["id"] if ex else None)
        if "barcode" in d and d["barcode"] and q("SELECT id FROM items WHERE barcode=? AND id<>?", d["barcode"], sid or -1): return toast("That barcode is already assigned to another item.", True)
        try:
            if sid: run(f"UPDATE {s.tn} SET " + ",".join(f"{k}=?" for k in d) + " WHERE id=?", *d.values(), sid)
            else: sid = run(f"INSERT INTO {s.tn}({','.join(d)}) VALUES({','.join('?' * len(d))})", *d.values()).lastrowid
        except sqlite3.IntegrityError as error: return toast(str(error), True)
        s.after_save(sid, d)
        s.clear(); s.refresh(); toast("Saved")
    def after_save(s, sid, data): pass

class ItemsPage(FormPage):
    title = "Items and Stock"; tn = "items"; nums = ("gst", "sp", "pp", "stock", "min"); cols = ("Item", "Barcode", "HSN", "GST %", "Sell", "Buy", "Stock"); widths = (210, 135, 85, 65, 90, 90, 100)
    fields = [("name", "Item name", "c"), ("barcode", "Barcode / SKU"), ("hsn", "HSN code"), ("unit", "Unit (pcs/set/ltr)"), ("gst", "GST %"), ("sp", "Selling price"), ("pp", "Buying price"), ("stock", "Stock now"), ("min", "Warn below")]
    def build(s):
        super().build()
        btn(s.actions, "Print selected label", s.print_selected).pack(side="left", padx=4)
        btn(s.actions, "Print all labels", s.print_all).pack(side="left", padx=4)
    def after_save(s, sid, data):
        if not data["barcode"]: run("UPDATE items SET barcode=? WHERE id=?", f"MS{sid:08d}", sid)
    def print_selected(s):
        if not s.tv.selection(): return toast("Select an item first.", True)
        try: print_item_labels(int(s.tv.selection()[0]))
        except (ValueError, OSError) as error: toast(str(error), True)
    def print_all(s):
        try: print_item_labels()
        except (ValueError, OSError) as error: toast(str(error), True)
    def refresh(s):
        s.tv.delete(*s.tv.get_children()); t = s.srch.get().lower()
        for r in q("SELECT * FROM items ORDER BY name"):
            if t in r["name"].lower() or t in (r["barcode"] or "").lower(): s.tv.insert("", "end", iid=r["id"], values=(r["name"], r["barcode"], r["hsn"], f"{r['gst']:g}", money(r["sp"]), money(r["pp"]), f"{r['stock']:g} {r['unit']}"), tags=("low",) if r["min"] > 0 and r["stock"] <= r["min"] else ())

class PartyPage(FormPage):
    title = "Customers and Suppliers"; tn = "parties"; nums = ("opening_balance",); cols = ("Name", "Role", "Phone", "GSTIN", "Balance"); widths = (240, 100, 120, 160, 200)
    fields = [("name", "Name", "c"), ("role", "Account type", "r", ["customer", "supplier", "both"]),
              ("phone", "Phone"), ("gstin", "GSTIN"), ("addr", "Address"),
              ("opening_balance", "Opening balance"), ("opening_date", "Opening date (YYYY-MM-DD)")]
    def after_save(s, sid, data):
        amount = data["opening_balance"]
        if amount < 0: raise ValueError("Opening balance cannot be negative.")
        if amount and not data.get("opening_date"):
            date = cfg().get("fiscal_start") or today()
            run("UPDATE parties SET opening_date=? WHERE id=?", date, sid)
        else: date = data.get("opening_date") or cfg().get("fiscal_start") or today()
        account = "AP" if data["role"] == "supplier" else "AR"
        with db:
            db.execute("DELETE FROM ledger_entries WHERE source_type='opening' AND source_id=?", (sid,))
            if amount:
                db.execute("INSERT INTO ledger_entries(party_id,account,entry_date,voucher,description,debit,credit,source_type,source_id) VALUES(?,?,?,?,?,?,?,?,?)",
                           (sid, account, date, "OPENING", "Opening balance", 0 if account == "AP" else amount,
                            amount if account == "AP" else 0, "opening", sid))
    def refresh(s):
        s.tv.delete(*s.tv.get_children()); t = s.srch.get().lower()
        for r in q("SELECT * FROM parties ORDER BY name"):
            if t in r["name"].lower():
                opening = -r["opening_balance"] if r["role"] == "supplier" else r["opening_balance"]
                b = opening + q("SELECT COALESCE(SUM(CASE WHEN kind='sale' THEN total-paid ELSE paid-total END),0) x FROM bills WHERE party=?", r["name"])[0]["x"]
                s.tv.insert("", "end", iid=r["id"], values=(r["name"], r["role"], r["phone"], r["gstin"], (money(abs(b)) + (" to receive" if b > 0 else " to pay")) if b else "-"))

class BillsPage(Page):
    title = "Bills"
    def build(s):
        s.k = "sale"; top = tk.Frame(s, bg=BG); top.pack(fill="x", padx=26, pady=4)
        s.b1 = btn(top, "Sales", lambda: s.set("sale"), "pri"); s.b1.pack(side="left", padx=(0, 6)); s.b2 = btn(top, "Purchases", lambda: s.set("purchase")); s.b2.pack(side="left")
        btn(top, "Delete", s.dele, "bad").pack(side="right"); btn(top, "Print selected", s.prt, "pri").pack(side="right", padx=8)
        f, s.tv = tview(s, ("No", "Date", "Party", "Total", "Balance due"), (150, 110, 320, 130, 130), 18); f.pack(fill="both", expand=True, padx=26, pady=6); s.tv.bind("<Double-1>", lambda e: s.prt())
    def set(s, k):
        s.k = k; s.b1.config(bg=AC if k == "sale" else P2); s.b2.config(bg=AC if k != "sale" else P2); s.refresh()
    def refresh(s):
        s.tv.delete(*s.tv.get_children())
        for r in q("SELECT * FROM bills WHERE kind=? ORDER BY id DESC LIMIT 500", s.k): s.tv.insert("", "end", iid=r["id"], values=(r["no"], r["date"], r["party"], money(r["total"]), money(r["total"] - r["paid"])))
    def prt(s):
        if s.tv.selection(): print_bill(int(s.tv.selection()[0]))
    def dele(s):
        if s.tv.selection() and mb.askyesno("Delete bill", "Delete this bill? Stock will be corrected."): delete_bill(int(s.tv.selection()[0])); s.refresh(); toast("Bill deleted")

class ReportPage(Page):
    title = "GST Report"
    def build(s):
        top = box(s); top.pack(fill="x", padx=26, pady=4); s.f = put(top, "From (YYYY-MM-DD)", 0, 0, 14); s.t = put(top, "To", 0, 1, 14); setv(s.f, today()[:8] + "01"); setv(s.t, today())
        btn(top, "Show", s.refresh, "pri").grid(row=1, column=2, padx=10)
        s.out = tk.Text(s, font=("Consolas", 12), bg=PN, fg=TX, relief="flat", insertbackground=TX, padx=20, pady=16); s.out.pack(fill="both", expand=True, padx=26, pady=8)
    def refresh(s):
        start, end = s.f.get(), s.t.get()
        total = lambda kind, col: q(f"SELECT COALESCE(SUM({col}),0) x FROM bills WHERE kind=? AND date BETWEEN ? AND ?", kind, start, end)[0]["x"]
        net_tax = total("sale", "tax") - total("purchase", "tax")
        ar_subledger = q("SELECT COALESCE(SUM(total-paid),0) x FROM bills WHERE kind='sale'")[0]["x"] + q("SELECT COALESCE(SUM(opening_balance),0) x FROM parties WHERE role IN ('customer','both')")[0]["x"]
        ap_subledger = q("SELECT COALESCE(SUM(total-paid),0) x FROM bills WHERE kind='purchase'")[0]["x"] + q("SELECT COALESCE(SUM(opening_balance),0) x FROM parties WHERE role='supplier'")[0]["x"]
        ar_ledger = q("SELECT COALESCE(SUM(debit-credit),0) x FROM ledger_entries WHERE account='AR'")[0]["x"]
        ap_ledger = q("SELECT COALESCE(SUM(credit-debit),0) x FROM ledger_entries WHERE account='AP'")[0]["x"]
        lines = [
            f"{start}  to  {end}", "",
            f"Sales (taxable)          {money(total('sale','sub')):>14}",
            f"GST collected on sales   {money(total('sale','tax')):>14}",
            f"Purchases (taxable)      {money(total('purchase','sub')):>14}",
            f"GST paid on purchases    {money(total('purchase','tax')):>14}", "",
            ("NET GST TO PAY" if net_tax >= 0 else "GST CREDIT") + f"           {money(abs(net_tax)):>14}",
            "", "Sales by GST rate", f"{'Rate':<8}{'Taxable':>14}{'GST':>12}"
        ]
        for row in q("SELECT l.gst g, SUM(l.taxable) t, SUM(l.tax) x FROM lines l JOIN bills b ON b.id=l.bill_id WHERE b.kind='sale' AND b.date BETWEEN ? AND ? GROUP BY l.gst", start, end):
            lines.append(f"{row['g']:g}%".ljust(8) + f"{money(row['t']):>14}{money(row['x']):>12}")
        lines += [
            "", f"Gross margin estimate    {money(total('sale','sub')-total('purchase','sub')):>14}",
            f"Still to receive         {money(ar_subledger):>14}", f"Still to pay             {money(ap_subledger):>14}", "",
            "AR RECONCILIATION", f"  Party sub-ledger        {money(ar_subledger):>14}",
            f"  AR ledger               {money(ar_ledger):>14}", f"  Difference              {money(ar_subledger-ar_ledger):>14}",
            "AP RECONCILIATION", f"  Party sub-ledger        {money(ap_subledger):>14}",
            f"  AP ledger               {money(ap_ledger):>14}", f"  Difference              {money(ap_subledger-ap_ledger):>14}", "",
            "Please confirm GST and accounting figures with your accountant before filing."
        ]
        s.out.delete("1.0", "end"); s.out.insert("1.0", "\n".join(lines))

class LedgerPage(Page):
    title = "Customer and Supplier Ledger"
    def build(s):
        top = box(s); top.pack(fill="x", padx=26, pady=4)
        s.account = ttk.Combobox(top, values=["Receivables (AR)", "Payables (AP)"], state="readonly", width=20); s.account.current(0)
        s.account.grid(row=1, column=0, padx=6, pady=8); s.account.bind("<<ComboboxSelected>>", lambda e: s.refresh_parties())
        s.party = ttk.Combobox(top, state="readonly", width=28); s.party.grid(row=1, column=1, padx=6); s.party.bind("<<ComboboxSelected>>", lambda e: s.refresh())
        s.start = put(top, "From", 0, 2, 12); s.end = put(top, "To", 0, 3, 12); setv(s.start, today()[:8] + "01"); setv(s.end, today())
        btn(top, "Show", s.refresh, "pri").grid(row=1, column=4, padx=6)
        btn(top, "Record receipt / payment", s.payment).grid(row=1, column=5, padx=6)
        btn(top, "Print statement", s.statement).grid(row=1, column=6, padx=6)
        s.balance = lab(s, "Select a customer or supplier.", AC, 12, True); s.balance.pack(anchor="w", padx=30, pady=(8, 4))
        fr, s.tv = tview(s, ("Date", "Voucher", "Description", "Debit", "Credit", "Balance"), (110, 150, 300, 120, 120, 130), 16)
        fr.pack(fill="both", expand=True, padx=26, pady=6)
        s.refresh_parties()
    def account_code(s): return "AR" if s.account.current() == 0 else "AP"
    def refresh_parties(s):
        names = [r["name"] for r in q("SELECT DISTINCT p.name FROM parties p JOIN ledger_entries l ON l.party_id=p.id WHERE l.account=? ORDER BY p.name", s.account_code())]
        s.party["values"] = names
        if names:
            if s.party.get() not in names: s.party.current(0)
            s.refresh()
        else:
            s.party.set(""); s.tv.delete(*s.tv.get_children()); s.balance.config(text="No ledger entries yet.")
    def refresh(s):
        name = s.party.get()
        if not name: return
        try:
            start = dt.date.fromisoformat(s.start.get()).isoformat(); end = dt.date.fromisoformat(s.end.get()).isoformat()
            if start > end: raise ValueError()
        except ValueError: return toast("Enter a valid date range in YYYY-MM-DD format.", True)
        account = s.account_code(); party = q("SELECT * FROM parties WHERE name=?", name)[0]
        rows = q("SELECT * FROM ledger_entries WHERE party_id=? AND account=? AND entry_date<=? ORDER BY entry_date,id", party["id"], account, end)
        opening = sum((r["debit"] - r["credit"]) if account == "AR" else (r["credit"] - r["debit"]) for r in rows if r["entry_date"] < start)
        balance = opening; s.tv.delete(*s.tv.get_children())
        s.tv.insert("", "end", values=(start, "", "Balance brought forward", "", "", money(balance)))
        for r in rows:
            if r["entry_date"] < start: continue
            balance += r["debit"] - r["credit"] if account == "AR" else r["credit"] - r["debit"]
            s.tv.insert("", "end", values=(r["entry_date"], r["voucher"], r["description"], money(r["debit"]), money(r["credit"]), money(balance)))
        label = "Receivable" if account == "AR" else "Payable"
        s.balance.config(text=f"{name}    {label}: Rs {money(balance)}    Period: {start} to {end}")
    def payment(s):
        name = s.party.get()
        if not name: return toast("Select a party first.", True)
        account = s.account_code(); kind = "sale" if account == "AR" else "purchase"
        invoices = q("SELECT id,no,date,total,paid FROM bills WHERE party=? AND kind=? AND total>paid ORDER BY date,id", name, kind)
        if not invoices: return toast("No unpaid invoices for this party.", True)
        win = tk.Toplevel(R); win.title("Record receipt" if account == "AR" else "Record payment"); win.configure(bg=BG); win.resizable(False, False); win.transient(R); win.grab_set()
        p = box(win); p.pack(fill="both", padx=18, pady=14)
        lab(p, "Invoice", TX, 10, True).grid(row=0, column=0, sticky="w", padx=5, pady=4)
        invoice = ttk.Combobox(p, state="readonly", width=42, values=[f"{r['no']}  |  Due Rs {money(r['total']-r['paid'])}" for r in invoices]); invoice.current(0); invoice.grid(row=1, column=0, columnspan=2, padx=5, pady=4)
        amount = put(p, "Amount", 1, 0, 16); date = put(p, "Date", 1, 1, 16); setv(date, today())
        mode = put(p, "Mode", 2, 0, 16, "r", ["Cash", "UPI", "Card", "Bank", "Cheque"]); mode.set("Cash")
        ref = put(p, "Reference", 2, 1, 24)
        def save():
            try: record_payment(invoices[invoice.current()]["id"], float(amount.get()), date.get().strip(), mode.get(), ref.get().strip())
            except (ValueError, TypeError) as error: return toast(str(error), True)
            win.destroy(); s.refresh_parties(); toast("Payment recorded")
        btn(p, "Save payment", save, "pri").grid(row=7, column=1, sticky="e", padx=5, pady=8)
    def statement(s):
        name = s.party.get()
        if not name: return toast("Select a party first.", True)
        s.refresh(); account = s.account_code(); party = q("SELECT * FROM parties WHERE name=?", name)[0]
        rows = q("SELECT * FROM ledger_entries WHERE party_id=? AND account=? AND entry_date<=? ORDER BY entry_date,id", party["id"], account, s.end.get())
        balance = sum((r["debit"] - r["credit"]) if account == "AR" else (r["credit"] - r["debit"]) for r in rows if r["entry_date"] < s.start.get())
        body = [f"<tr><td>{E(s.start.get())}</td><td></td><td>Balance brought forward</td><td></td><td></td><td>{money(balance)}</td></tr>"]
        for r in rows:
            if r["entry_date"] < s.start.get(): continue
            balance += r["debit"] - r["credit"] if account == "AR" else r["credit"] - r["debit"]
            body.append(f"<tr><td>{E(r['entry_date'])}</td><td>{E(r['voucher'])}</td><td>{E(r['description'])}</td><td>{money(r['debit'])}</td><td>{money(r['credit'])}</td><td>{money(balance)}</td></tr>")
        heading = "Customer Statement" if account == "AR" else "Supplier Statement"
        kind = "sale" if account == "AR" else "purchase"; aging = [0.0, 0.0, 0.0, 0.0]; now = dt.date.today()
        for invoice in q("SELECT date,total,paid FROM bills WHERE party=? AND kind=? AND total>paid", name, kind):
            try: age = max(0, (now - dt.date.fromisoformat(invoice["date"])).days)
            except ValueError: age = 0
            aging[0 if age <= 30 else 1 if age <= 60 else 2 if age <= 90 else 3] += invoice["total"] - invoice["paid"]
        html = f"<h1>{E(heading)}</h1><p>{E(name)} | {E(s.start.get())} to {E(s.end.get())}</p><table border=1 cellspacing=0 cellpadding=6><tr><th>Date</th><th>Voucher</th><th>Description</th><th>Debit</th><th>Credit</th><th>Balance</th></tr>{''.join(body)}</table><p>Closing balance: Rs {money(balance)}</p><h2>Aging summary</h2><table border=1 cellspacing=0 cellpadding=6><tr><th>0-30 days</th><th>31-60 days</th><th>61-90 days</th><th>91+ days</th></tr><tr>{''.join(f'<td>Rs {money(value)}</td>' for value in aging)}</tr></table><script>window.print()</script>"
        path = os.path.join(BASE, "bills", f"statement-{party['id']}.html"); os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as file: file.write(html)
        webbrowser.open(pathlib.Path(path).as_uri())

class AgingPage(Page):
    title = "Receivables and Payables Aging"
    def build(s):
        top = box(s); top.pack(fill="x", padx=26, pady=4)
        s.account = ttk.Combobox(top, values=["Receivables (AR)", "Payables (AP)"], state="readonly", width=20); s.account.current(0); s.account.grid(row=1, column=0, padx=6, pady=8)
        btn(top, "Refresh", s.refresh, "pri").grid(row=1, column=1, padx=6)
        fr, s.tv = tview(s, ("Party", "Voucher", "Date", "Current 0-30", "31-60", "61-90", "91+", "Outstanding"), (220, 130, 110, 130, 110, 110, 110, 130), 18)
        fr.pack(fill="both", expand=True, padx=26, pady=6)
    def refresh(s):
        account = "AR" if s.account.current() == 0 else "AP"; kind = "sale" if account == "AR" else "purchase"
        s.tv.delete(*s.tv.get_children()); now = dt.date.today()
        for r in q("SELECT party,no,date,total,paid FROM bills WHERE kind=? AND total>paid ORDER BY party,date", kind):
            try: age = max(0, (now - dt.date.fromisoformat(r["date"])).days)
            except ValueError: age = 0
            due = r["total"] - r["paid"]; bucket = 0 if age <= 30 else 1 if age <= 60 else 2 if age <= 90 else 3
            vals = [0.0] * 4; vals[bucket] = due
            s.tv.insert("", "end", values=(r["party"], r["no"], r["date"], *(money(v) if v else "-" for v in vals), money(due)))

class CSVImportPage(Page):
    title = "Sales and Purchase CSV Import"
    def build(s):
        s.groups = []; top = box(s); top.pack(fill="x", padx=26, pady=4)
        s.kind = ttk.Combobox(top, values=["Sales", "Purchases"], state="readonly", width=14); s.kind.current(0); s.kind.grid(row=1, column=0, padx=6, pady=8)
        btn(top, "Choose CSV", s.choose, "pri").grid(row=1, column=1, padx=6)
        s.import_btn = btn(top, "Import valid batch", s.do); s.import_btn.grid(row=1, column=2, padx=6); s.import_btn.config(state="disabled")
        s.summary = lab(s, "Use one row per invoice item. Required columns: Date, InvoiceNo, party name, ItemName, Quantity, UnitPrice or UnitCost, TaxRate, TotalAmount, TransactionID.", MU, 10)
        s.summary.pack(anchor="w", padx=30, pady=6)
        fr, s.tv = tview(s, ("Invoice", "Party", "Date", "Lines", "Total", "Status"), (160, 260, 110, 80, 120, 360), 15)
        fr.pack(fill="both", expand=True, padx=26, pady=6)
    def choose(s):
        path = filedialog.askopenfilename(filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if not path: return
        kind = "sale" if s.kind.current() == 0 else "purchase"
        try: s.groups, _errors = parse_invoice_csv(path, kind)
        except (OSError, UnicodeError, ValueError, csv.Error) as error: return toast(str(error), True)
        for group in s.groups:
            if q("SELECT 1 FROM bills WHERE kind=? AND no=?", kind, group["invoice_no"]): group["errors"].append("Invoice number already exists.")
            if q("SELECT 1 FROM bills WHERE ref=?", group["transaction_id"]): group["errors"].append("Transaction ID already imported.")
        s.path = path; s.tv.delete(*s.tv.get_children()); problem_count = 0; grand_total = 0
        for group in s.groups:
            amount = totals(group["lines"], 0)["total"] if group["lines"] else 0; grand_total += amount
            status = "; ".join(group["errors"]) or "Ready"
            problem_count += bool(group["errors"])
            s.tv.insert("", "end", values=(group["invoice_no"], group["party"], group["date"], len(group["lines"]), money(amount), status))
        ready = bool(s.groups) and not problem_count
        s.import_btn.config(state="normal" if ready else "disabled")
        s.summary.config(text=f"{len(s.groups)} invoices, {sum(len(g['lines']) for g in s.groups)} item lines, Rs {money(grand_total)}. " +
                         (f"{problem_count} invoice(s) need correction." if problem_count else "Preview is valid; import will be all-or-nothing."))
    def do(s):
        kind = "sale" if s.kind.current() == 0 else "purchase"
        try: count, amount = import_invoice_batch(s.groups, kind, s.path)
        except (ValueError, sqlite3.Error) as error: return toast(str(error), True)
        s.summary.config(text=f"Imported {count} invoices, Rs {money(amount)}. Inventory, ledgers, and import history updated.")
        s.import_btn.config(state="disabled"); s.groups = []

class SettingsPage(Page):
    title = "Settings"
    def build(s):
        c = cfg(); s.e = {}; f = box(s); f.pack(fill="x", padx=26, pady=6)
        rows = [("shop_name", "Shop name"), ("phone", "Phone"), ("addr", "Address"), ("gstin", "Your GSTIN"), ("state", "State"), ("default_gst", "GST % for new items"), ("fiscal_start", "Fiscal start date"), ("cash_ledger", "Cash ledger name"), ("bank_ledger", "Bank ledger name"), ("expense_ledger", "Expense ledger name"), ("prefix", "Bill number start"), ("next_no", "Next bill number"),
                ("heading", "Bill heading"), ("terms", "Terms on bill"), ("footer", "Last line on bill"), ("color", "Bill colour (#hex)"), ("upi", "UPI ID"), ("bank", "Bank details")]
        for i, (k, l) in enumerate(rows): s.e[k] = put(f, l, i // 3, i % 3, 28); setv(s.e[k], c[k])
        for i in range(3): f.columnconfigure(i, weight=1)
        g = box(s); g.pack(fill="x", padx=26, pady=6); lab(g, "FIRM LOGO  (shown on printed bills)", MU, 9, True).grid(row=0, column=0, sticky="w", padx=14, pady=(12, 4))
        s.pv = tk.Label(g, bg=P2, fg=MU, width=22, height=5, text="No logo yet"); s.pv.grid(row=1, column=0, rowspan=2, padx=14, pady=(0, 12))
        btn(g, "Upload logo", s.pick_logo, "pri").grid(row=1, column=1, sticky="sw", padx=6); btn(g, "Remove", s.rm_logo).grid(row=2, column=1, sticky="nw", padx=6, pady=4)
        s.paper = put(g, "Paper size", 0, 2, 16, "r", ["A4", "A5", "Thermal 80mm"]); s.paper.set(c["paper"]); s.h = tk.IntVar(value=int(c["show_hsn"])); s.d = tk.IntVar(value=int(c["show_disc"]))
        chk(g, "Show HSN on bill", s.h).grid(row=1, column=3, padx=14, sticky="w"); chk(g, "Show discount on bill", s.d).grid(row=2, column=3, padx=14, sticky="w")
        bt = tk.Frame(s, bg=BG); bt.pack(anchor="w", padx=26, pady=10); btn(bt, "Save settings", s.save, "pri").pack(side="left"); btn(bt, "Backup data now", s.backup).pack(side="left", padx=8); s.preview()
    def preview(s):
        c = cfg()
        if c["logo_b64"] and c["logo_mime"] != "image/jpeg":
            try: im = tk.PhotoImage(data=c["logo_b64"]); k = max(1, im.width() // 150 + 1); s.im = im.subsample(k, k); return s.pv.config(image=s.im, text="", width=0, height=0)
            except tk.TclError: pass
        s.pv.config(image="", text="Logo set (no preview)" if c["logo_b64"] else "No logo yet", width=22, height=5)
    def pick_logo(s):
        p = filedialog.askopenfilename(filetypes=[("Images", "*.png *.jpg *.jpeg *.gif")])
        if not p: return
        if os.path.getsize(p) > 1_500_000: return toast("Logo is too big. Use a file under 1.5 MB.", True)
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif"}[p.rsplit(".", 1)[-1].lower()]
        run("UPDATE settings SET v=? WHERE k='logo_b64'", base64.b64encode(open(p, "rb").read()).decode()); run("UPDATE settings SET v=? WHERE k='logo_mime'", mime); s.preview(); toast("Logo saved")
    def rm_logo(s): run("UPDATE settings SET v='' WHERE k IN ('logo_b64','logo_mime')"); s.preview(); toast("Logo removed")
    def save(s):
        for k, e in s.e.items(): run("UPDATE settings SET v=? WHERE k=?", e.get().strip(), k)
        for k, v in (("paper", s.paper.get()), ("show_hsn", str(s.h.get())), ("show_disc", str(s.d.get()))): run("UPDATE settings SET v=? WHERE k=?", v, k)
        R.app.side_name.config(text=cfg()["shop_name"]); toast("Settings saved")
    def backup(s):
        d = os.path.join(BASE, "backups"); os.makedirs(d, exist_ok=True); p = os.path.join(d, f"msgo_{dt.datetime.now():%Y%m%d_%H%M}.db"); db.commit(); shutil.copy(os.path.join(BASE, "shop.db"), p); toast("Backup saved in the backups folder")

class App:
    def __init__(s):
        global R; R = tk.Tk(); R.app = s; R.title("MSgo"); R.geometry("1280x780"); R.minsize(1060, 660); R.configure(bg=BG)
        try: R.iconbitmap(os.path.join(RES, "msgo.ico"))
        except Exception: pass
        style(); side = tk.Frame(R, bg=PN, width=220); side.pack(side="left", fill="y"); side.pack_propagate(False)
        hd = tk.Frame(side, bg=PN); hd.pack(fill="x", padx=16, pady=(22, 18)); c = tk.Canvas(hd, width=44, height=44, bg=PN, highlightthickness=0); c.pack(side="left"); draw_logo(c)
        t = tk.Frame(hd, bg=PN); t.pack(side="left", padx=10); tk.Label(t, text="MSgo", bg=PN, fg=TX, font=(FN, 17, "bold")).pack(anchor="w"); s.side_name = tk.Label(t, text=cfg()["shop_name"], bg=PN, fg=MU, font=(FN, 8), wraplength=120, justify="left"); s.side_name.pack(anchor="w")
        s.body = tk.Frame(R, bg=BG); s.body.pack(side="left", fill="both", expand=True)
        spec = [("Dashboard", Dash, None), ("New Sale", Bill, "sale"), ("Purchase", Bill, "purchase"),
            ("Items", ItemsPage, None), ("Customers", PartyPage, None), ("Bills", BillsPage, None),
            ("GST Report", ReportPage, None), ("CSV Import", CSVImportPage, None),
            ("Party Ledger", LedgerPage, None), ("Aging Report", AgingPage, None), ("Settings", SettingsPage, None)]
        s.pages, s.nav, s.cur = [], [], None
        for i, (name, cls, kind) in enumerate(spec):
            row = tk.Frame(side, bg=PN, cursor="hand2"); row.pack(fill="x", pady=1); bar = tk.Frame(row, bg=PN, width=3); bar.pack(side="left", fill="y")
            l = tk.Label(row, text=f"{name}", bg=PN, fg=MU, font=(FN, 11), anchor="w", padx=16, pady=10); l.pack(side="left", fill="x", expand=True)
            for w in (row, l):
                w.bind("<Button-1>", lambda e, i=i: s.show(i)); w.bind("<Enter>", lambda e, i=i: s.hover(i, 1)); w.bind("<Leave>", lambda e, i=i: s.hover(i, 0))
            s.nav.append((row, bar, l)); s.pages.append(cls(s, kind)); R.bind(f"<F{i + 1}>", lambda e, i=i: s.show(i))
        btn(side, "Company setup", s.setup_wizard).pack(side="bottom", fill="x", padx=12, pady=(0, 8))
        tk.Label(side, text="F1-F11 navigate | Ctrl+N sale | Ctrl+I import", bg=PN, fg=HV, font=(FN, 8)).pack(side="bottom", pady=8)
        s.show(0)
        R.bind("<Control-n>", lambda e: s.show(1))
        R.bind("<Control-i>", lambda e: s.show(7))
        R.bind("<Control-l>", lambda e: s.show(8))
        R.bind("<Control-f>", s.quick_find)
        R.bind("<Control-p>", s.print_current)
        R.bind("<Alt-n>", s.quick_payment)
        R.bind("<Control-s>", s.save_current)
        if cfg().get("wizard_done") != "1": R.after(250, s.setup_wizard)
    def quick_find(s, event=None):
        s.show(4); s.pages[4].srch.focus_set(); return "break"
    def print_current(s, event=None):
        if isinstance(s.pages[s.cur], BillsPage): s.pages[s.cur].prt()
        return "break"
    def quick_payment(s, event=None):
        if isinstance(s.pages[s.cur], LedgerPage): s.pages[s.cur].payment()
        return "break"
    def save_current(s, event=None):
        page = s.pages[s.cur]
        if isinstance(page, Bill): page.save()
        elif isinstance(page, SettingsPage): page.save()
        return "break"
    def setup_wizard(s):
        c = cfg(); win = tk.Toplevel(R); win.title("Company setup"); win.configure(bg=BG); win.resizable(False, False); win.transient(R); win.grab_set()
        p = box(win); p.pack(fill="both", padx=20, pady=14)
        lab(p, "COMPANY", AC, 9, True).grid(row=0, column=0, columnspan=4, sticky="w", padx=5, pady=(4, 0))
        fields = {}
        for i, (key, title) in enumerate((("shop_name", "Company name"), ("addr", "Address"), ("gstin", "GSTIN"), ("state", "State"), ("fiscal_start", "Fiscal start YYYY-MM-DD"), ("default_gst", "Default GST %"), ("cash_ledger", "Cash account"), ("bank_ledger", "Bank account"), ("expense_ledger", "Expense account"))):
            fields[key] = put(p, title, 1 + i // 3, i % 3, 25); setv(fields[key], c.get(key, ""))
        lab(p, "OPENING BALANCES", AC, 9, True).grid(row=8, column=0, columnspan=4, sticky="w", padx=5, pady=(10, 0))
        for i, (key, title, default) in enumerate((("ar_name", "Opening customer", ""), ("ar_amount", "Receivable amount", "0"),
                                                    ("ap_name", "Opening supplier", ""), ("ap_amount", "Payable amount", "0"))):
            fields[key] = put(p, title, 5 + i // 2, i % 2, 27); setv(fields[key], default)
        def finish():
            try:
                fiscal = dt.date.fromisoformat(fields["fiscal_start"].get().strip()).isoformat() if fields["fiscal_start"].get().strip() else today()
                gst = float(fields["default_gst"].get() or 0); ar = float(fields["ar_amount"].get() or 0); ap = float(fields["ap_amount"].get() or 0)
                if not 0 <= gst <= 100 or ar < 0 or ap < 0: raise ValueError()
                if ar and not fields["ar_name"].get().strip(): raise ValueError("Enter the customer name for its opening balance.")
                if ap and not fields["ap_name"].get().strip(): raise ValueError("Enter the supplier name for its opening balance.")
            except ValueError as error: return toast(str(error) or "Check the date, tax rate, and opening balance amounts.", True)
            with db:
                for key in ("shop_name", "addr", "gstin", "state", "cash_ledger", "bank_ledger", "expense_ledger"):
                    db.execute("UPDATE settings SET v=? WHERE k=?", (fields[key].get().strip(), key))
                for key, value in (("fiscal_start", fiscal), ("default_gst", str(gst)), ("wizard_done", "1")):
                    db.execute("UPDATE settings SET v=? WHERE k=?", (value, key))
                for role, name_key, amount in (("customer", "ar_name", ar), ("supplier", "ap_name", ap)):
                    name = fields[name_key].get().strip()
                    if name:
                        db.execute("INSERT INTO parties(name,role,opening_balance,opening_date) VALUES(?,?,?,?) ON CONFLICT(name) DO UPDATE SET role=excluded.role,opening_balance=excluded.opening_balance,opening_date=excluded.opening_date",
                                   (name, role, amount, fiscal))
                        party = db.execute("SELECT id FROM parties WHERE name=?", (name,)).fetchone()
                        account = "AR" if role == "customer" else "AP"
                        db.execute("DELETE FROM ledger_entries WHERE source_type='opening' AND source_id=?", (party["id"],))
                        if amount:
                            db.execute("INSERT INTO ledger_entries(party_id,account,entry_date,voucher,description,debit,credit,source_type,source_id) VALUES(?,?,?,?,?,?,?,?,?)",
                                       (party["id"], account, fiscal, "OPENING", "Opening balance", 0 if account == "AP" else amount,
                                        amount if account == "AP" else 0, "opening", party["id"]))
            s.side_name.config(text=cfg()["shop_name"]); win.destroy(); s.pages[4].refresh(); toast("Company setup saved")
        btn(p, "Save setup", finish, "pri").grid(row=9, column=2, sticky="e", padx=6, pady=12)
        btn(p, "Cancel", win.destroy).grid(row=9, column=1, sticky="e", padx=6, pady=12)
    def hover(s, i, on):
        if i != s.cur: s.nav[i][0].config(bg=P2 if on else PN); s.nav[i][2].config(bg=P2 if on else PN, fg=TX if on else MU)
    def show(s, i):
        if s.cur is not None: s.pages[s.cur].pack_forget(); r, b, l = s.nav[s.cur]; r.config(bg=PN); b.config(bg=PN); l.config(bg=PN, fg=MU)
        s.cur = i; r, b, l = s.nav[i]; r.config(bg=P2); b.config(bg=AC); l.config(bg=P2, fg=TX); s.pages[i].pack(fill="both", expand=True); s.pages[i].refresh()

def main():
    App(); R.mainloop()

if __name__ == "__main__":
    main()
