import os
import sys

# Add the project root directory to the Python path
project_home = os.path.dirname(os.path.abspath(__file__))
if project_home not in sys.path:
    sys.path.insert(0, project_home)

# Load environment variables if .env exists
try:
    from dotenv import load_dotenv
    env_file = os.path.join(project_home, '.env')
    if os.path.exists(env_file):
        load_dotenv(env_file)
except ImportError:
    pass

from app import create_app

# PythonAnywhere WSGI handler looks for 'application'
application = create_app()

if __name__ == '__main__':
    application.run(port=5000, debug=False)
