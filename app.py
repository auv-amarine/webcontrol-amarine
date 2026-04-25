import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template
from flask_socketio import SocketIO
import paramiko
import time
import re
import threading
import socket

app = Flask(__name__, template_folder='.')
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# === KONFIGURASI JETSON (DIJALANKAN LOKAL DI JETSON) ===
JETSON_IP = '127.0.0.1' 
JETSON_USER = 'amarine'
JETSON_PASS = '123890' 

# PATH BARU SESUAI PERMINTAAN KAPTEN OZZA
BASE_PATH = "~/webcontrol-amarine/"

# Alias Mapping Lengkap (Gazebo, Vision, ArduPilot, MAVRoS, ROS2)
ALIAS_MAP = {
    # GAZEBO
    'c1a': 'gz sim -v 3 -r sauvc_qualification.world',
    'c1b': 'gz sim -v 3 -r sauvc_final.world',
    
    # VISION (STREAMER LOKAL)
    'c2v': f'python3 {BASE_PATH}streamer.py', 
    'c2y': f'source /opt/ros/humble/setup.bash && python3 {BASE_PATH}yolo_streamer.py', 
    
    # VISION (YOLO VIA DOCKER - FIX RCLPY)
    'cmd_detect_1': "docker start be537dc7c441 && docker exec be537dc7c441 bash -c 'source /opt/ros/humble/setup.bash && cd /ultralytics && export ROS_DOMAIN_ID=0 && python3 detect_ros.py'",
    'cmd_detect_2': "docker start be537dc7c441 && docker exec be537dc7c441 bash -c 'source /opt/ros/humble/setup.bash && cd /ultralytics && export ROS_DOMAIN_ID=0 && python3 detect_ros_2.py'",
    
    # ARDUPILOT SITL
    'cmd_sitl': 'cd ~/ardupilot && Tools/autotest/sim_vehicle.py -L RATBeach -v ArduSub -f vectored --model=JSON --out=udp:127.0.0.1:14550',
    
    # MAVROS (UDP JALUR BARU)
    't1_mavros': "source /opt/ros/humble/setup.bash && source ~/ros2_ws/install/setup.bash && ros2 launch mavros apm.launch fcu_url:=udp://:14550@localhost:14555",
    
    # ROS2 TOOLS (DROPDOWN)
    'c4a': 'source /opt/ros/humble/setup.bash && cd ~/ros2_ws && colcon build --packages-select sauvc26_code',
    'cmd_test': 'source /opt/ros/humble/setup.bash && source ~/ros2_ws/install/setup.bash && ros2 run sauvc26_code test', 
    'cmd_arm': 'source /opt/ros/humble/setup.bash && source ~/ros2_ws/install/setup.bash && ros2 run sauvc26_code arm',
    'c4c': 'source /opt/ros/humble/setup.bash && source ~/ros2_ws/install/setup.bash && ros2 run sauvc26_code qualification',
    'cmd_final': 'source /opt/ros/humble/setup.bash && source ~/ros2_ws/install/setup.bash && ros2 run sauvc26_code final',
    'echo_yolo': 'source /opt/ros/humble/setup.bash && source ~/ros2_ws/install/setup.bash && ros2 topic echo /yolo_target_coord',
    
    # TOMBOL PANEL KIRI (SERVICE)
    't2_arm': "source /opt/ros/humble/setup.bash && source ~/ros2_ws/install/setup.bash && ros2 service call /mavros/set_mode mavros_msgs/srv/SetMode \"{custom_mode: 'MANUAL'}\" && ros2 service call /mavros/cmd/arming mavros_msgs/srv/CommandBool \"{value: True}\"",
    't3_auto': "source /opt/ros/humble/setup.bash && source ~/ros2_ws/install/setup.bash && ros2 run simple_boat boat_mover_node",
    
    # MONITOR & BRIDGE
    'c0a': f'{BASE_PATH}monitor.sh',
    'c2a': "source /opt/ros/humble/setup.bash && ros2 run ros_gz_bridge parameter_bridge '/front_camera@sensor_msgs/msg/Image@gz.msgs.Image'"
}

is_ssh_connected = False
ssh_client = None

def stream_output(channel, console_target):
    while True:
        try:
            if channel.recv_ready():
                output = channel.recv(1024).decode('utf-8', errors='ignore')
                socketio.emit('terminal_output', {'text': output, 'target': console_target})
            if channel.exit_status_ready():
                socketio.emit('terminal_output', {'text': f"\n[SYSTEM] Proses selesai.\n", 'target': console_target, 'type': 'success'})
                break
        except: break
        time.sleep(0.1)

def hardware_monitor_thread():
    global is_ssh_connected, ssh_client
    while is_ssh_connected and ssh_client:
        try:
            stdin, stdout, stderr = ssh_client.exec_command("tegrastats --interval 2000")
            while is_ssh_connected:
                line = stdout.readline()
                if not line: break
                try:
                    ram_search = re.search(r'RAM (\d+)', line)
                    mem = round(int(ram_search.group(1)) / 1024, 1) if ram_search else 0.0
                    cpu_search = re.search(r'CPU \[(.*?)\]', line)
                    cpu_val = sum(int(c) for c in re.findall(r'(\d+)%', cpu_search.group(1))) // 6 if cpu_search else 0
                    gpu_search = re.search(r'GR3D.*? (\d+)%', line)
                    gpu_val = int(gpu_search.group(1)) if gpu_search else 0
                    temps = re.findall(r'@([\d\.]+)C', line)
                    temp_val = max([float(t) for t in temps if 0 < float(t) < 150]) if temps else 0.0
                    watt_search = re.search(r'(?:VDD_IN|POM_5V_IN|VDD_MUX) (\d+)', line)
                    watt_val = round(int(watt_search.group(1)) / 1000, 1) if watt_search else 0.0
                    socketio.emit('hardware_stats', {'cpu': cpu_val, 'gpu': gpu_val, 'mem': mem, 'temp': temp_val, 'watt': watt_val})
                except: pass
        except: break
        time.sleep(2)

@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('camera_data')
def handle_camera_data(data):
    socketio.emit('video_feed', data)

@socketio.on('run_alias')
def handle_alias(data):
    alias_id = data.get('id')
    target = data.get('target', 'Main')
    if not is_ssh_connected: return
    
    if alias_id == 'kill_specific':
        cmd_id = data.get('cmd', '')
        kill_map = {
            'c4a': 'colcon',
            'cmd_test': 'test',
            'cmd_arm': 'arm', 
            'c4c': 'qualification', 
            'cmd_final': 'final',
            'echo_yolo': 'echo',
            'c2v': 'streamer.py',
            'c2y': 'yolo_streamer.py',
            'cmd_detect_1': 'detect_ros.py',
            'cmd_detect_2': 'detect_ros_2.py',
            'cmd_sitl': 'sim_vehicle.py',
            't1_mavros': 'mavros',           
            't2_arm': 'ros2',   
            't3_auto': 'boat_mover_node'     
        }
        proc_name = kill_map.get(cmd_id, cmd_id)
        ssh_client.exec_command(f"pkill -9 -f {proc_name}")
        socketio.emit('terminal_output', {'text': f"[SYSTEM] Menutup proses: {proc_name}", 'target': target, 'type': 'error'})
        return

    if alias_id in ALIAS_MAP:
        cmd = ALIAS_MAP[alias_id]
        try:
            transport = ssh_client.get_transport()
            chan = transport.open_session()
            chan.get_pty()
            chan.exec_command(cmd)
            threading.Thread(target=stream_output, args=(chan, target)).start()
            socketio.emit('terminal_output', {'text': f"[LOCAL] Menjalankan: {cmd}\n", 'target': target, 'type': 'cmd'})
        except Exception as e:
            socketio.emit('terminal_output', {'text': f"[ERROR] Gagal: {str(e)}", 'target': target, 'type': 'error'})

@socketio.on('request_ssh_connect')
def handle_ssh_connect():
    global is_ssh_connected, ssh_client
    try:
        ssh_client = paramiko.SSHClient()
        ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh_client.connect(JETSON_IP, username=JETSON_USER, password=JETSON_PASS, timeout=5.0)
        is_ssh_connected = True
        socketio.start_background_task(hardware_monitor_thread)
        socketio.emit('ssh_status', {'connected': True})
    except Exception as e:
        socketio.emit('ssh_status', {'connected': False})

@socketio.on('request_ssh_disconnect')
def handle_ssh_disconnect():
    global is_ssh_connected, ssh_client
    if ssh_client: ssh_client.close()
    is_ssh_connected = False
    socketio.emit('ssh_status', {'connected': False})

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except: return "127.0.0.1"

if __name__ == '__main__':
    local_ip = get_local_ip()
    print(f"🚀 SERVER GCS AKTIF: http://{local_ip}:5000")
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)
