import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
import cv2
import numpy as np
import socketio
import time
import base64

# Inisialisasi SocketIO Client untuk kirim data ke app.py
sio = socketio.Client()
SERVER_URL = 'http://127.0.0.1:5000'

def connect_to_server():
    """Fungsi untuk memastikan koneksi ke Flask Backend (app.py)"""
    while not sio.connected:
        try:
            print(f"[YOLO STREAMER] Mencoba terhubung ke GCS {SERVER_URL}...")
            sio.connect(SERVER_URL)
        except Exception as e:
            # Tunggu 3 detik jika gagal sebelum mencoba lagi
            time.sleep(3)

class YoloWebStreamer(Node):
    def __init__(self):
        super().__init__('yolo_web_streamer')
        
        # QoS Profile SANGAT KRUSIAL! 
        # Kebanyakan publisher kamera/YOLO menggunakan BEST_EFFORT.
        # Jika subscriber menggunakan RELIABLE (default), data tidak akan pernah masuk.
        qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=10
        )

        # Subscribe ke topik hasil deteksi YOLO
        self.subscription = self.create_subscription(
            Image,
            '/yolo_result',
            self.image_callback,
            qos)
            
        self.last_emit_time = 0.0
        self.frames_sent = 0
        self.get_logger().info('🚀 GCS MALANG MBOIS: Menunggu feed dari /yolo_result...')

    def image_callback(self, msg):
        if not sio.connected:
            return

        # 1. LIMITER FPS (~15 FPS)
        # Mencegah penumpukan data di Socket.IO yang bisa bikin web UI freeze
        current_time = time.time()
        if current_time - self.last_emit_time < 0.06:
            return
        self.last_emit_time = current_time

        try:
            # 2. KONVERSI DATA MENTAH KE NUMPY (BYPASS CV_BRIDGE)
            # Ditambahkan .copy() agar array bersifat writable untuk proses OpenCV
            cv_image = np.frombuffer(msg.data, dtype=np.uint8).reshape((msg.height, msg.width, -1)).copy()
            
            # Jika format aslinya RGB, balik ke BGR agar warna di web normal (tidak tertukar biru/merah)
            if hasattr(msg, 'encoding') and 'rgb' in msg.encoding.lower():
                cv_image = cv2.cvtColor(cv_image, cv2.COLOR_RGB2BGR)

            # 3. KOMPRESI KE JPEG
            # Kualitas 40% sudah sangat cukup untuk pantauan GCS via WiFi/Tailscale
            encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 40] 
            success, buffer = cv2.imencode('.jpg', cv_image, encode_param)
            
            if not success:
                return
                
            # 4. ENCODE KE BASE64 & EMIT KE BACKEND
            b64_string = base64.b64encode(buffer).decode('utf-8')
            sio.emit('camera_data', b64_string)
            
            # Indikator log setiap 50 frame agar tidak memenuhi terminal
            self.frames_sent += 1
            if self.frames_sent % 50 == 0:
                self.get_logger().info(f'✅ YOLO Streaming: {self.frames_sent} frame terkirim.')

        except Exception as e:
            self.get_logger().error(f"Gagal memproses gambar YOLO: {e}")

def main(args=None):
    # Pastikan SocketIO terkoneksi sebelum ROS jalan
    connect_to_server()
    
    # Inisialisasi ROS 2 Humble
    rclpy.init(args=args)
    yolo_streamer = YoloWebStreamer()
    
    try:
        # Loop utama ROS
        rclpy.spin(yolo_streamer)
    except KeyboardInterrupt:
        print("\n[YOLO STREAMER] Dihentikan oleh user.")
    finally:
        yolo_streamer.destroy_node()
        rclpy.shutdown()
        if sio.connected:
            sio.disconnect()

if __name__ == '__main__':
    main()
