# Vignesh Growth Lab POS (விக்னேஷ் குரோத் லேப்)

A modern, high-performance, bilingual (Tamil & English) Point of Sale (POS) and Retail Management System with GST billing, WhatsApp receipt integration, thermal print support, inventory control, and real-time sales reporting.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![Flask](https://img.shields.io/badge/Flask-3.0.3-green)
![GST Compliant](https://img.shields.io/badge/GST-Tamil%20Nadu%20(33)-purple)

---

## Key Features

- **⚡ Fast POS Terminal**:
  - Barcode / HSN product search with keyboard shortcut (`Ctrl+K` or `/`).
  - Fast keyboard shortcuts: `F2` (Complete Sale), `F3` (Print Bill), `F4` (WhatsApp Bill), `Esc` (Modal/Search clear).
  - Responsive layout designed for both laptop screens (ThinkPad 14" @ 150% scaling) and large external monitors (1080p/1440p).
  - Pinned/docked bottom checkout action bar — buttons are never hidden or cut off.

- **📱 WhatsApp Bill Dispatch**:
  - Direct WhatsApp billing via `https://wa.me/91<phone>?text=...`.
  - Itemized receipt summary with live receipt view link.

- **🧾 Full GST Compliance**:
  - Dual Tamil Nadu CGST + SGST automated split and B2B GSTIN recording.
  - Thermal receipt printing with bilingual Tamil/English receipt layout.

- **📊 Management Dashboard**:
  - Real-time revenue, order count, top-selling items, and day-close calculations.

---

## Quick Start (Local Development)

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Seed initial demo data
python seed_data.py

# 3. Launch application
python run_app.py
```
Open [http://127.0.0.1:5000](http://127.0.0.1:5000) in your browser.

---

## Deployment on PythonAnywhere

See full deployment guide in [PYTHONANYWHERE_DEPLOY.md](PYTHONANYWHERE_DEPLOY.md).

WSGI entrypoint is located in `wsgi.py`.
