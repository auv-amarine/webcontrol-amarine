# Amarine Web Control v1.0

Web-based Ground Control Station for Autonomous Underwater Vehicle (AUV). This application enables real-time control and monitoring of an underwater robot through a web interface.

## Installation

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Run the application:
```bash
python app.py
```

3. Access in browser:
```
http://localhost:5000
```

## File Structure

- `app.py` - Flask server with SocketIO
- `streamer.py` - Video streaming from OpenCV
- `yolo_streamer.py` - YOLO detection streaming via ROS2
- `index.html` - Web interface

