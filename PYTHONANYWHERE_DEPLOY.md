# PythonAnywhere Hosting Guide — Vignesh Growth Lab POS

Follow these simple steps to deploy and run **Vignesh Growth Lab POS** on PythonAnywhere (Free or Hacker tier) for client demonstrations.

---

### Step 1: Open Bash Console on PythonAnywhere
1. Log in to [PythonAnywhere](https://www.pythonanywhere.com).
2. Go to the **Consoles** tab and click **Bash**.

---

### Step 2: Clone the Repository & Setup Virtual Environment
In the Bash console, run:
```bash
# Clone the repository
git clone https://github.com/Vignesh-10032000/vignesh-pos.git

# Navigate into the project
cd vignesh-pos

# Create a virtual environment with Python 3.10
virtualenv --python=python3.10 venv

# Activate the virtual environment
source venv/bin/activate

# Install required dependencies
pip install -r requirements.txt
```

---

### Step 3: Initialize Database & Seed Demo Data
In the same console, run:
```bash
python seed_data.py
```
This prepares the SQLite database with products, categories, customers, and GST taxes.

---

### Step 4: Configure Web App in PythonAnywhere Dashboard
1. Go to the **Web** tab in PythonAnywhere.
2. Click **Add a new web app**.
3. Choose **Manual configuration** and select **Python 3.10**.
4. In the Web settings:
   - **Source code**: `/home/<your-username>/vignesh-pos`
   - **Working directory**: `/home/<your-username>/vignesh-pos`
   - **Virtualenv**: `/home/<your-username>/vignesh-pos/venv`

---

### Step 5: Configure the WSGI File
1. Under the **Code** section on the Web tab, click the link to your **WSGI configuration file** (e.g. `/var/www/<your-username>_pythonanywhere_com_wsgi.py`).
2. Replace all its contents with the following:

```python
import os
import sys

# Replace with your PythonAnywhere username
username = '<your-username>'
project_home = f'/home/{username}/vignesh-pos'

if project_home not in sys.path:
    sys.path.insert(0, project_home)

# Set base URL for WhatsApp receipts
os.environ['RECEIPT_BASE_URL'] = f'https://{username}.pythonanywhere.com'

from wsgi import application
```

3. Save the file.

---

### Step 6: Reload the Web App
1. Go back to the **Web** tab.
2. Click the big green **Reload <your-username>.pythonanywhere.com** button.
3. Open `https://<your-username>.pythonanywhere.com` in your browser.

Your live demo of **Vignesh Growth Lab POS** is now ready! 🎉
