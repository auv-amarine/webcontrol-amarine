import cv2
import socketio
import time
import base64

# Inisialisasi SocketIO Client
sio = socketio.Client()

# URL Server (Karena app.py jalan di Jetson yang sama, kita pakai localhost)
SERVER_URL = 'http://127.0.0.1:5000'

# Setup Kamera (Gunakan index 0 untuk kamera USB default)
# Jika kamera kamu tidak terbaca, ubah CAMERA_INDEX menjadi 1, 2, atau /dev/video0
CAMERA_INDEX = 0 
cap = cv2.VideoCapture(CAMERA_INDEX)

# Turunkan resolusi agar streaming lebih ringan & lancar untuk GCS
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

@sio.event
def connect():
    print(f"[STREAMER] Berhasil terhubung ke server GCS di {SERVER_URL}")

@sio.event
def disconnect():
    print("[STREAMER] Terputus dari server GCS.")

def connect_to_server():
    """Fungsi tangguh untuk terus mencoba koneksi ke server jika terputus"""
    while not sio.connected:
        try:
            print(f"[STREAMER] Mencoba terhubung ke {SERVER_URL}...")
            sio.connect(SERVER_URL)
        except Exception as e:
            print(f"[STREAMER] Gagal terhubung. Coba lagi dalam 3 detik...")
            time.sleep(3)

def stream_camera():
    connect_to_server()
    
    print("[STREAMER] Memulai transmisi video Kamera USB...")
    while True:
        ret, frame = cap.read()
        if not ret:
            print("[STREAMER] Gagal membaca frame dari kamera. Coba re-inisialisasi...")
            time.sleep(1)
            continue

        # Kompresi frame ke JPEG agar ringan
        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 60]
        _, buffer = cv2.imencode('.jpg', frame, encode_param)
        
        # Ubah ke base64 string untuk dikirim via SocketIO
        b64_string = base64.b64encode(buffer).decode('utf-8')
        
        # Kirim data ke server (app.py)
        if sio.connected:
            try:
                sio.emit('camera_data', b64_string)
            except Exception as e:
                pass
        else:
            connect_to_server()

        # Limit framerate (~20 FPS) agar tidak memberatkan CPU
        time.sleep(0.05)

if __name__ == '__main__':
    try:
        print("[STREAMER] Inisialisasi Kamera...")
        time.sleep(2)
        if not cap.isOpened():
            print("[STREAMER] ERROR: Kamera tidak terdeteksi! Pastikan kamera terhubung.")
        else:
            stream_camera()
    except KeyboardInterrupt:
        print("\n[STREAMER] Dihentikan oleh user.")
    finally:
        cap.release()
        if sio.connected:
            sio.disconnect()
