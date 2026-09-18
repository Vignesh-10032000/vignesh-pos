import os
import sys
import webbrowser
from threading import Timer

# Set root directory for PyInstaller bundle context
if getattr(sys, 'frozen', False):
    base_path = sys._MEIPASS
else:
    base_path = os.path.dirname(os.path.abspath(__file__))

from app import create_app

def open_browser():
    webbrowser.open_new("http://127.0.0.1:5000/")

if __name__ == '__main__':
    # Force current working directory to exe directory so the SQLite DB builds locally next to it
    if getattr(sys, 'frozen', False):
        os.chdir(os.path.dirname(sys.executable))
    
    # Start browser auto-launch after 1.5 seconds
    Timer(1.5, open_browser).start()
    
    app = create_app()
    app.run(port=5000, debug=False)
