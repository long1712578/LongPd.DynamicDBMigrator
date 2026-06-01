import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from web.app import app, UPLOAD_FOLDER

if __name__ == '__main__':
    print("=" * 60)
    print("Starting the web server...")
    print(f"Upload folder: {UPLOAD_FOLDER}")
    print("=" * 60)

    app.run(host='0.0.0.0', port=5000, debug=True)