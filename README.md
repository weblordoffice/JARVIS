brew services start mongodb/brew/mongodb-community
cd /Users/binodyadav/Desktop/JARVIS/backend
python3 -m pip install -r requirements.txt
python3 -m uvicorn main:app --host 127.0.0.1 --port 8765
