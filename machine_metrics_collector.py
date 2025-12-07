#!/usr/bin/env python3
import yaml  # Add this import at the top
import socket
import json
import time as time_module
import random
import threading
from datetime import datetime, time as time_class, timedelta
import tkinter as tk
from tkinter import ttk, messagebox, font
import psutil
import platform
import logging
from logging.handlers import RotatingFileHandler
#import automationhat  # Only used when SIMULATION = False
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ----------------------  JSON file for counter persistence
COUNTERS_FILE = "production_counters.json"

def load_counters():
    """Load counters from JSON file"""
    try:
        with open(COUNTERS_FILE, 'r') as f:
            counters = json.load(f)
            return counters.get('qtBon', 0), counters.get('qtRejet', 0)
    except (FileNotFoundError, json.JSONDecodeError):
        return 0, 0  # Default values if file doesn't exist or is invalid

def save_counters(qtBon, qtRejet):
    """Save counters to JSON file"""
    try:
        with open(COUNTERS_FILE, 'w') as f:
            json.dump({'qtBon': qtBon, 'qtRejet': qtRejet}, f)
    except Exception as e:
        logger.error(f"Error saving counters: {str(e)}")
# ---------------------- Configuration ----------------------
def parse_time(time_str):
    """Convert time string in format 'HH:MM:SS' to time object"""
    if isinstance(time_str, str):
        return datetime.strptime(time_str, "%H:%M:%S").time()
    return time_str

def load_config():
    try:
        with open('config.yaml', 'r') as f:
            config = yaml.safe_load(f)
        
        # Convert string times to time objects for shift schedule
        for shift in config['SHIFT_SCHEDULE']:
            shift['start'] = parse_time(shift['start'])
            shift['end'] = parse_time(shift['end'])
        
        return config
    except Exception as e:
        print(f"Error loading config: {e}")
        return None

def save_config(config):
    """Save configuration to YAML file, converting time objects to strings"""
    try:
        # Create a copy to avoid modifying the original
        config_copy = config.copy()
        config_copy['SHIFT_SCHEDULE'] = [shift.copy() for shift in config['SHIFT_SCHEDULE']]
        
        # Convert time objects to strings
        for shift in config_copy['SHIFT_SCHEDULE']:
            shift['start'] = shift['start'].strftime("%H:%M:%S")
            shift['end'] = shift['end'].strftime("%H:%M:%S")
        
        with open('config.yaml', 'w') as f:
            yaml.dump(config_copy, f)
        return True
    except Exception as e:
        logger.error(f"Error saving config: {e}")
        return False

# Load configuration
config = load_config()
if config is None:
    raise SystemExit("Failed to load configuration")

# Assign configuration to variables
SIMULATION = config['SIMULATION']
SERVER_IP = config['SERVER_IP']
SERVER_PORT = config['SERVER_PORT']
MACHINE_ID = config['MACHINE_ID']
SAMPLING_INTERVAL = config['SAMPLING_INTERVAL']
MAX_LOG_SIZE = config['MAX_LOG_SIZE']
LOG_BACKUP_COUNT = config['LOG_BACKUP_COUNT']
SHIFT_SCHEDULE = config['SHIFT_SCHEDULE']
STOP_REASONS = config['STOP_REASONS']

# ---------------------- Logging Setup ----------------------
def setup_logging():
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    file_handler = RotatingFileHandler(
        'production_monitor.log',
        maxBytes=MAX_LOG_SIZE,
        backupCount=LOG_BACKUP_COUNT
    )
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s'
    ))
    
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(
        '%(levelname)s: %(message)s'
    ))
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger

logger = setup_logging()
# ---------------------- Email Notification ----------------------

def send_email_notification(stop_reason, stop_time):
    """Send email notification about machine stop in a separate thread"""
    def send_email():
        try:
            if 'EMAIL_CONFIG' not in config:
                logger.warning("Email configuration not found")
                return

            email_config = config['EMAIL_CONFIG']
            recipient = email_config['RECIPIENTS'].get(stop_reason, email_config['RECIPIENTS']['Other'])
            
            msg = MIMEMultipart()
            msg['From'] = email_config['EMAIL_FROM']
            msg['To'] = recipient
            msg['Subject'] = f"Machine {MACHINE_ID} Stopped - {stop_reason}"
            
            body = f"""
            Machine Stop Notification
            
            Machine ID: {MACHINE_ID}
            Stop Reason: {stop_reason}
            Stop Time: {stop_time.strftime('%Y-%m-%d %H:%M:%S')}
            Current Shift: {get_current_shift()}
            
            This is an automated notification.
            """
            
            msg.attach(MIMEText(body, 'plain'))
            
            with smtplib.SMTP(email_config['SMTP_SERVER'], email_config['SMTP_PORT']) as server:
                server.starttls()
                server.login(email_config['EMAIL_FROM'], email_config['EMAIL_PASSWORD'])
                server.send_message(msg)
                
            logger.info(f"Email notification sent to {recipient} for stop reason: {stop_reason}")
        except Exception as e:
            logger.error(f"Failed to send email notification: {str(e)}")
    
    # Start email thread
    email_thread = threading.Thread(target=send_email, daemon=True)
    email_thread.start()

# ---------------------- Global Counters ----------------------
qtBon, qtRejet = load_counters()  # Load counters from file
stop_event = threading.Event()
last_successful_transmission = None
transmission_errors = 0
current_stop_reason = None
stop_time = None
last_stop_info = {"reason": None, "duration": None, "start_time": None}
historical_metrics = []

# ---------------------- System Metrics Functions ----------------------
def get_cpu_temperature():
    try:
        if platform.system() == 'Linux':
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                temp = int(f.read()) / 1000
            return temp
        elif platform.system() == 'Windows':
            try:
                import wmi
                w = wmi.WMI(namespace="root\\wmi")
                temp_info = w.MSAcpi_ThermalZoneTemperature()[0]
                return (temp_info.CurrentTemperature - 2732) / 10.0
            except:
                return None
        else:
            return None
    except Exception as e:
        logger.warning(f"Failed to get CPU temperature: {str(e)}")
        return None

def get_system_metrics():
    try:
        net_io = psutil.net_io_counters()
        
        metrics = {
            'cpu_percent': psutil.cpu_percent(interval=1),
            'memory_percent': psutil.virtual_memory().percent,
            'temperature': get_cpu_temperature(),
            'disk_percent': psutil.disk_usage('/').percent,
            'network_sent': net_io.bytes_sent,
            'network_recv': net_io.bytes_recv,
            'boot_time': psutil.boot_time(),
            'process_count': len(psutil.pids())
        }
        
        historical_metrics.append(metrics)
        if len(historical_metrics) > 60:
            historical_metrics.pop(0)
            
        return metrics
    except Exception as e:
        logger.error(f"Error getting system metrics: {str(e)}")
        return {
            'cpu_percent': 0,
            'memory_percent': 0,
            'temperature': None,
            'disk_percent': 0,
            'network_sent': 0,
            'network_recv': 0,
            'boot_time': 0,
            'process_count': 0
        }

# ---------------------- Simulation Setup ----------------------
if SIMULATION:
    def simulate_production():
        global qtBon, qtRejet
        logger.info("Starting production simulation thread")
        while not stop_event.is_set():
            try:
                if current_stop_reason is None:
                    if random.random() < 0.7:
                        qtBon += 1
                    if random.random() < 0.1:
                        qtRejet += 1
                time_module.sleep(0.5)
            except Exception as e:
                logger.error(f"Simulation error: {str(e)}")
                time_module.sleep(1)
    
    sim_thread = threading.Thread(target=simulate_production, daemon=True)
    sim_thread.start()

# ---------------------- Shift Detection ----------------------
def get_current_shift():
    try:
        now = datetime.now().time()
        
        for shift in SHIFT_SCHEDULE:
            if shift['start'] > shift['end']:
                if now >= shift['start'] or now < shift['end']:
                    return shift['name']
            else:
                if shift['start'] <= now < shift['end']:
                    return shift['name']
        
        return "UNKNOWN"
    except Exception as e:
        logger.error(f"Shift detection error: {str(e)}")
        return "ERROR"

# ---------------------- Data Collection ----------------------
def collect_data():
    global qtBon, qtRejet, current_stop_reason, stop_time, last_stop_info

    try:
        if SIMULATION:
            state = "RUNNING" if random.random() > 0.2 and current_stop_reason is None else "IDLE"
        else:
            if automationhat.input.one.read() and current_stop_reason is None:
                qtBon += 1
            if automationhat.input.two.read() and current_stop_reason is None:
                qtRejet += 1
            state_pin = automationhat.input.three.read()
            state = "RUNNING" if state_pin and current_stop_reason is None else "IDLE"

        metrics = get_system_metrics()

        display_state = state
        if current_stop_reason is not None:
            stop_time_str = stop_time.strftime("%H:%M:%S") if stop_time else "Unknown time"
            display_state = f"STOPPED ({current_stop_reason} at {stop_time_str})"
        elif last_stop_info["reason"] is not None:
            duration_str = str(last_stop_info["duration"]).split('.')[0]
            display_state = f"RUNNING (Last stop: {last_stop_info['reason']} for {duration_str})"

        payload = {
            "machine_id": MACHINE_ID,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "cycle_count": qtBon + qtRejet,
            "state": display_state,
            "qtBon": qtBon,
            "qtRejet": qtRejet,
            "shift": get_current_shift(),
            "system_metrics": metrics,
            "software_version": "2.1.0",
            "transmission_status": {
                "last_success": last_successful_transmission,
                "error_count": transmission_errors
            },
            "stop_reason": current_stop_reason,
            "stop_time": stop_time.isoformat() if stop_time else None,
            "last_stop_reason": last_stop_info["reason"],
            "last_stop_duration": str(last_stop_info["duration"]) if last_stop_info["duration"] else None,
            "last_stop_start": last_stop_info["start_time"].isoformat() if last_stop_info["start_time"] else None
        }

        return payload
    except Exception as e:
        logger.error(f"Data collection error: {str(e)}")
        return {
            "machine_id": MACHINE_ID,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "error": str(e)
        }

# ---------------------- TCP Send ----------------------
def send_data(payload):
    global last_successful_transmission, transmission_errors
    
    if SIMULATION:
        time_module.sleep(0.1)
        success = random.random() < 0.9
        if success:
            last_successful_transmission = datetime.now().isoformat()
        else:
            transmission_errors += 1
        return success
    else:
        try:
            with socket.create_connection((SERVER_IP, SERVER_PORT), timeout=5) as sock:
                sock.sendall(json.dumps(payload).encode("utf-8") + b"\n")
                ack = sock.recv(16).decode().strip()
                if ack == "ACK":
                    last_successful_transmission = datetime.now().isoformat()
                    return True
                transmission_errors += 1
                return False
        except socket.error as e:
            transmission_errors += 1
            logger.error(f"Socket error: {str(e)}")
            return False
        except Exception as e:
            transmission_errors += 1
            logger.error(f"Transmission error: {str(e)}")
            return False

# ---------------------- Touchscreen-Optimized GUI ----------------------
class ProductionMonitor:
    def __init__(self, root):
        # Slightly darker colors for better visibility
        self.BG_COLOR = "#1a2b3c"
        self.FG_COLOR = "#f8f9fa"
        self.ACCENT_COLOR = "#4e9af1"
        self.GOOD_COLOR = "#2ecc71"
        self.BAD_COLOR = "#e74c3c"
        self.WARNING_COLOR = "#f39c12"
        self.SHIFT_COLORS = {
            "Shift1": "#3498db",
            "Shift2": "#9b59b6",
            "Shift3": "#e67e22",
            "UNKNOWN": "#95a5a6"
        }
        
        self.root = root
        self.style = ttk.Style()
        self.setup_gui()
        
        self.last_rate_calc_time = datetime.now()
        self.last_total_parts = 0
        #self.last_oee_calc_time = datetime.now()
        
        self.update_gui()
    
    def setup_gui(self):
        self.root.title(f"Production Monitor ({'SIMULATION' if SIMULATION else 'LIVE'} MODE)")
        self.root.geometry("800x480")
        self.root.attributes('-fullscreen', True)
        self.root.configure(bg=self.BG_COLOR)
        
        # Configure styles with smaller fonts
        self.style.theme_use('clam')
        self.style.configure('.', background=self.BG_COLOR, foreground=self.FG_COLOR)
        self.style.configure('TFrame', background=self.BG_COLOR)
        self.style.configure('Header.TLabel', font=('Arial', 16, 'bold'), background=self.BG_COLOR)
        self.style.configure('Status.TLabel', font=('Arial', 14), background=self.BG_COLOR)
        self.style.configure('Counter.TLabel', font=('Arial', 24, 'bold'))
        self.style.configure('Large.TButton', font=('Arial', 14), padding=8)
        self.style.configure('Metrics.TLabel', font=('Arial', 12), background=self.BG_COLOR)
        self.style.configure("Custom.Horizontal.TProgressbar", thickness=20)

        # Main grid layout - using grid exclusively
        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(0, weight=1)
        
        main_frame = ttk.Frame(self.root)
        main_frame.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        
        # Configure main frame grid
        main_frame.grid_rowconfigure(0, weight=0)  # Header
        main_frame.grid_rowconfigure(1, weight=0)  # Status
        main_frame.grid_rowconfigure(2, weight=1)  # Production
        main_frame.grid_rowconfigure(3, weight=1)  # System
        main_frame.grid_rowconfigure(4, weight=0)  # Buttons
        main_frame.grid_rowconfigure(5, weight=0)  # Status bar
        main_frame.grid_columnconfigure(0, weight=1)

        # Header section
        header_frame = ttk.Frame(main_frame)
        header_frame.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        
        ttk.Label(header_frame, text=f"Machine: {MACHINE_ID}", style='Header.TLabel').pack(side=tk.LEFT)
        
        time_frame = ttk.Frame(header_frame)
        time_frame.pack(side=tk.RIGHT)
        self.date_var = tk.StringVar()
        ttk.Label(time_frame, textvariable=self.date_var, style='Header.TLabel').pack(side=tk.RIGHT)
        self.time_var = tk.StringVar()
        ttk.Label(time_frame, textvariable=self.time_var, style='Header.TLabel').pack(side=tk.RIGHT)

        # Status bar (shift and state)
        status_bar = ttk.Frame(main_frame)
        status_bar.grid(row=1, column=0, sticky="ew", pady=(0, 5))
        
        self.shift_var = tk.StringVar()
        self.shift_indicator = tk.Label(status_bar, textvariable=self.shift_var, 
                                      font=('Arial', 14, 'bold'), bd=1, relief=tk.RAISED,
                                      padx=5, pady=2)
        self.shift_indicator.pack(side=tk.LEFT, padx=5)
        
        self.state_var = tk.StringVar()
        self.state_indicator = tk.Label(status_bar, textvariable=self.state_var, 
                                      font=('Arial', 14, 'bold'), bd=1, relief=tk.RAISED,
                                      padx=5, pady=2)
        self.state_indicator.pack(side=tk.RIGHT, padx=5)

        # Production metrics frame
        production_frame = ttk.LabelFrame(main_frame, text="Production", padding=5)
        production_frame.grid(row=2, column=0, sticky="nsew", padx=2, pady=2)
        
        # Configure production frame grid
        production_frame.grid_rowconfigure(0, weight=1)
        production_frame.grid_columnconfigure(0, weight=1)
        production_frame.grid_columnconfigure(1, weight=1)
        production_frame.grid_columnconfigure(2, weight=1)
        
        # Counters
        good_frame = ttk.Frame(production_frame)
        good_frame.grid(row=0, column=0, sticky="nsew", padx=5, pady=2)
        ttk.Label(good_frame, text="GOOD", font=('Arial', 14)).pack()
        self.good_counter = ttk.Label(good_frame, text="0", style='Counter.TLabel')
        self.good_counter.pack()
        
        total_frame = ttk.Frame(production_frame)
        total_frame.grid(row=0, column=1, sticky="nsew", padx=5, pady=2)
        ttk.Label(total_frame, text="TOTAL", font=('Arial', 14)).pack()
        self.total_counter = ttk.Label(total_frame, text="0", style='Counter.TLabel')
        self.total_counter.pack()
        
        reject_frame = ttk.Frame(production_frame)
        reject_frame.grid(row=0, column=2, sticky="nsew", padx=5, pady=2)
        ttk.Label(reject_frame, text="REJECT", font=('Arial', 14)).pack()
        self.reject_counter = ttk.Label(reject_frame, text="0", style='Counter.TLabel')
        self.reject_counter.pack()
        
        # Rates row
        rate_frame = ttk.Frame(production_frame)
        rate_frame.grid(row=1, column=0, columnspan=3, sticky="ew", pady=2)
        
        self.reject_rate_var = tk.StringVar(value="Rej: 0.0%")
        ttk.Label(rate_frame, textvariable=self.reject_rate_var, font=('Arial', 12)).pack(side=tk.LEFT, padx=5)
        
        self.rate_var = tk.StringVar(value="Rate: 0/min")
        ttk.Label(rate_frame, textvariable=self.rate_var, font=('Arial', 12)).pack(side=tk.LEFT, padx=5)
        
        #self.oee_var = tk.StringVar(value="OEE: 0%")
        #ttk.Label(rate_frame, textvariable=self.oee_var, font=('Arial', 12)).pack(side=tk.LEFT, padx=5)

        # System metrics frame
        system_frame = ttk.LabelFrame(main_frame, text="System", padding=5)
        system_frame.grid(row=3, column=0, sticky="nsew", padx=2, pady=2)
        
        # Configure system frame grid
        system_frame.grid_rowconfigure(0, weight=1)
        system_frame.grid_rowconfigure(1, weight=1)
        system_frame.grid_rowconfigure(2, weight=1)
        system_frame.grid_rowconfigure(3, weight=1)
        system_frame.grid_rowconfigure(4, weight=1)
        system_frame.grid_columnconfigure(0, weight=1)
        
        # CPU
        cpu_frame = ttk.Frame(system_frame)
        cpu_frame.grid(row=0, column=0, sticky="ew", pady=1)
        ttk.Label(cpu_frame, text="CPU:", width=8, font=('Arial', 12)).pack(side=tk.LEFT)
        self.cpu_var = tk.StringVar(value="0%")
        self.cpu_label = ttk.Label(cpu_frame, textvariable=self.cpu_var, width=6, font=('Arial', 12))
        self.cpu_label.pack(side=tk.LEFT)
        self.cpu_bar = ttk.Progressbar(cpu_frame, orient=tk.HORIZONTAL, length=150, 
                                     mode='determinate', style='Custom.Horizontal.TProgressbar')
        self.cpu_bar.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)
        
        # Memory
        mem_frame = ttk.Frame(system_frame)
        mem_frame.grid(row=1, column=0, sticky="ew", pady=1)
        ttk.Label(mem_frame, text="Mem:", width=8, font=('Arial', 12)).pack(side=tk.LEFT)
        self.mem_var = tk.StringVar(value="0%")
        self.mem_label = ttk.Label(mem_frame, textvariable=self.mem_var, width=6, font=('Arial', 12))
        self.mem_label.pack(side=tk.LEFT)
        self.mem_bar = ttk.Progressbar(mem_frame, orient=tk.HORIZONTAL, length=150, 
                                     mode='determinate', style='Custom.Horizontal.TProgressbar')
        self.mem_bar.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)
        
        # Temperature
        temp_frame = ttk.Frame(system_frame)
        temp_frame.grid(row=2, column=0, sticky="ew", pady=1)
        ttk.Label(temp_frame, text="Temp:", width=8, font=('Arial', 12)).pack(side=tk.LEFT)
        self.temp_var = tk.StringVar(value="0°C")
        self.temp_label = ttk.Label(temp_frame, textvariable=self.temp_var, width=6, font=('Arial', 12))
        self.temp_label.pack(side=tk.LEFT)
        self.temp_bar = ttk.Progressbar(temp_frame, orient=tk.HORIZONTAL, length=150, 
                                      mode='determinate', style='Custom.Horizontal.TProgressbar')
        self.temp_bar.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)
        
        # Disk
        disk_frame = ttk.Frame(system_frame)
        disk_frame.grid(row=3, column=0, sticky="ew", pady=1)
        ttk.Label(disk_frame, text="Disk:", width=8, font=('Arial', 12)).pack(side=tk.LEFT)
        self.disk_var = tk.StringVar(value="0%")
        self.disk_label = ttk.Label(disk_frame, textvariable=self.disk_var, width=6, font=('Arial', 12))
        self.disk_label.pack(side=tk.LEFT)
        self.disk_bar = ttk.Progressbar(disk_frame, orient=tk.HORIZONTAL, length=150, 
                                      mode='determinate', style='Custom.Horizontal.TProgressbar')
        self.disk_bar.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)
        
        # Network and uptime
        net_uptime_frame = ttk.Frame(system_frame)
        net_uptime_frame.grid(row=4, column=0, sticky="ew", pady=1)
        
        ttk.Label(net_uptime_frame, text="Net:", width=8, font=('Arial', 12)).pack(side=tk.LEFT)
        self.network_status = tk.Label(net_uptime_frame, text="Disconnected", font=('Arial', 12), width=10)
        self.network_status.pack(side=tk.LEFT, padx=5)
        
        ttk.Label(net_uptime_frame, text="Up:", font=('Arial', 12)).pack(side=tk.LEFT)
        self.uptime_var = tk.StringVar(value="0:00:00")
        ttk.Label(net_uptime_frame, textvariable=self.uptime_var, font=('Arial', 12)).pack(side=tk.LEFT)

        # Control buttons
        button_frame = ttk.Frame(main_frame)
        button_frame.grid(row=4, column=0, sticky="ew", pady=(5, 0))
        
        button_config = {
            'style': 'Large.TButton',
            'padding': 5,
            'width': 8
        }
        
        reset_btn = ttk.Button(button_frame, text="RESET", command=self.reset_counters, **button_config)
        reset_btn.grid(row=0, column=0, padx=2, sticky="ew")
        
        settings_btn = ttk.Button(button_frame, text="SET", command=self.show_settings, **button_config)
        settings_btn.grid(row=0, column=1, padx=2, sticky="ew")
        
        self.stop_btn = ttk.Button(button_frame, text="STOP", command=self.toggle_stop_run, **button_config)
        self.stop_btn.grid(row=0, column=2, padx=2, sticky="ew")
        
        exit_btn = ttk.Button(button_frame, text="EXIT", command=self.confirm_exit, **button_config)
        exit_btn.grid(row=0, column=3, padx=2, sticky="ew")
        
        # Configure button columns
        button_frame.columnconfigure(0, weight=1)
        button_frame.columnconfigure(1, weight=1)
        button_frame.columnconfigure(2, weight=1)
        button_frame.columnconfigure(3, weight=1)

        # Status bar
        self.status_var = tk.StringVar(value="System ready")
        status_bar = ttk.Label(main_frame, textvariable=self.status_var, relief=tk.SUNKEN, 
                             anchor=tk.W, font=('Arial', 10))
        status_bar.grid(row=5, column=0, sticky="ew", pady=(5, 0))
    def update_metric_color(self, label, bar, value, warn_threshold, crit_threshold, max_value=100):
        if value is None:
            color = self.BAD_COLOR
            value = 0
        elif value >= crit_threshold:
            color = self.BAD_COLOR
        elif value >= warn_threshold:
            color = self.WARNING_COLOR
        else:
            color = self.GOOD_COLOR
        
        label.config(foreground=color)
        bar['value'] = min(value, max_value)
        self.style.configure("Custom.Horizontal.TProgressbar", background=color)
        
    def update_gui(self):
        try:
            data = collect_data()
            
            # Update time
            now = datetime.now()
            self.time_var.set(now.strftime("%H:%M:%S"))
            self.date_var.set(now.strftime("%Y-%m-%d"))
            
            # Update shift and state
            current_shift = data.get('shift', 'UNKNOWN')
            self.shift_var.set(f"Shift: {current_shift}")
            self.shift_indicator.config(bg=self.SHIFT_COLORS.get(current_shift, self.SHIFT_COLORS["UNKNOWN"]))
            
            state = data.get('state', 'UNKNOWN')
            self.state_var.set(state)
            if "STOPPED" in state:
                self.state_indicator.config(bg=self.BAD_COLOR, fg='white')
            else:
                self.state_indicator.config(bg=self.GOOD_COLOR, fg='black')
            
            # Update counters
            good_count = data.get('qtBon', 0)
            reject_count = data.get('qtRejet', 0)
            total_count = good_count + reject_count
            
            self.good_counter.config(text=str(good_count))
            self.reject_counter.config(text=str(reject_count))
            self.total_counter.config(text=str(total_count))
            
            # Calculate rejection rate
            if total_count > 0:
                reject_rate = (reject_count / total_count) * 100
                self.reject_rate_var.set(f"Rejection Rate: {reject_rate:.1f}%")
            
            # Update system metrics
            metrics = data.get('system_metrics', {})
            
            cpu_value = metrics.get('cpu_percent', 0)
            self.cpu_var.set(f"{cpu_value:.0f}%")
            self.update_metric_color(self.cpu_label, self.cpu_bar, cpu_value, 70, 90)
            
            mem_value = metrics.get('memory_percent', 0)
            self.mem_var.set(f"{mem_value:.0f}%")
            self.update_metric_color(self.mem_label, self.mem_bar, mem_value, 70, 90)
            
            temp_value = metrics.get('temperature', 0)
            self.temp_var.set(f"{temp_value:.0f}°C" if temp_value is not None else "N/A")
            self.update_metric_color(self.temp_label, self.temp_bar, temp_value, 60, 80, 100)
            
            disk_value = metrics.get('disk_percent', 0)
            self.disk_var.set(f"{disk_value:.0f}%")
            self.update_metric_color(self.disk_label, self.disk_bar, disk_value, 70, 90)
            
            # Calculate production rate (parts per minute)
            current_time = datetime.now()
            time_diff = (current_time - self.last_rate_calc_time).total_seconds() / 60
            if time_diff > 1:  # Update rate every minute
                parts_diff = total_count - self.last_total_parts
                rate = parts_diff / time_diff
                self.rate_var.set(f"Production Rate: {rate:.1f}/min")
                self.last_rate_calc_time = current_time
                self.last_total_parts = total_count
            
            # Calculate OEE (simplified)
            #if current_stop_reason is None:
            #    if hasattr(self, 'last_oee_calc_time'):
            #        total_time = (current_time - self.last_oee_calc_time).total_seconds()
            #        running_time = total_time  # Simplified
            #        oee = (running_time / total_time) * 100
            #        self.oee_var.set(f"OEE: {oee:.0f}%")
            #    self.last_oee_calc_time = current_time
            
            # Update network status
            if last_successful_transmission:
                time_since = (datetime.now() - datetime.fromisoformat(last_successful_transmission)).total_seconds()
                if time_since < 10:
                    self.network_status.config(text="Connected", fg=self.GOOD_COLOR)
                elif time_since < 30:
                    self.network_status.config(text="Warning", fg=self.WARNING_COLOR)
                else:
                    self.network_status.config(text="Disconnected", fg=self.BAD_COLOR)
            else:
                self.network_status.config(text="Disconnected", fg=self.BAD_COLOR)
            
            # Update uptime
            if 'boot_time' in metrics:
                uptime_seconds = time_module.time() - metrics['boot_time']
                uptime_str = str(timedelta(seconds=int(uptime_seconds)))
                self.uptime_var.set(uptime_str)
            
            # Update status bar
            status_msg = "System ready"
            if last_successful_transmission:
                status_msg = f"Last transmission: {datetime.fromisoformat(last_successful_transmission).strftime('%H:%M:%S')}"
                if transmission_errors > 0:
                    status_msg += f" | Errors: {transmission_errors}"
            self.status_var.set(status_msg)
            
            # Update STOP/RUN button
            self.update_stop_button()
            
        except Exception as e:
            logger.error(f"GUI update error: {str(e)}")
            self.status_var.set(f"Error: {str(e)}")
        
        self.root.after(500, self.update_gui)
    
    def update_stop_button(self):
        if current_stop_reason is None:
            self.stop_btn.config(text="STOP", style='Large.TButton')
        else:
            self.stop_btn.config(text="RUN", style='Large.TButton')
    
    def reset_counters(self):
        global qtBon, qtRejet
        qtBon = 0
        qtRejet = 0
        save_counters(qtBon, qtRejet)  # Save the reset counters
        self.status_var.set(f"Counters reset at {datetime.now().strftime('%H:%M:%S')}")
        logger.info("Production counters reset")
    
    def toggle_stop_run(self):
        if current_stop_reason is None:
            self.show_stop_reasons()
        else:
            self.set_stop_reason(None)
    
    def show_stop_reasons(self):
        stop_window = tk.Toplevel(self.root)
        stop_window.title("Select Stop Reason")
        stop_window.geometry("400x350")
        stop_window.resizable(False, False)
        stop_window.configure(bg=self.BG_COLOR)
        
        # Position window near the stop button
        stop_window.geometry(f"+{self.root.winfo_x()+200}+{self.root.winfo_y()+600}")
        
        # Make modal
        stop_window.grab_set()
        stop_window.focus_set()
        
        main_frame = ttk.Frame(stop_window, padding=20)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        ttk.Label(main_frame, text="Select Stop Reason:", font=('Arial', 16)).pack(pady=10)
        
        self.stop_reason_var = tk.StringVar()
        
        for reason in STOP_REASONS:
            rb = ttk.Radiobutton(
                main_frame, 
                text=reason, 
                variable=self.stop_reason_var, 
                value=reason,
                style='TRadiobutton'
            )
            rb.pack(anchor=tk.W, pady=5)
        
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=10)
        
        ttk.Button(
            button_frame, 
            text="Confirm", 
            command=lambda: self.set_stop_reason(stop_window),
            style='Large.TButton'
        ).pack(side=tk.RIGHT, padx=5)
        
        ttk.Button(
            button_frame, 
            text="Cancel", 
            command=stop_window.destroy,
            style='Large.TButton'
        ).pack(side=tk.RIGHT, padx=5)
    
    def set_stop_reason(self, window):
        global current_stop_reason, stop_time, last_stop_info
        reason = self.stop_reason_var.get() if window else ""
        now = datetime.now()
        
        if reason == "":  # Clear stop
            if current_stop_reason is not None:
                duration = now - stop_time
                last_stop_info = {
                    "reason": current_stop_reason,
                    "duration": duration,
                    "start_time": stop_time
                }
                duration_str = str(duration).split('.')[0]
                self.status_var.set(f"Running (Last stop: {current_stop_reason} for {duration_str})")
                logger.info(f"System running after stop: {current_stop_reason} for {duration_str}")
            
            current_stop_reason = None
            stop_time = None
        elif reason:
            current_stop_reason = reason
            stop_time = now
            time_str = stop_time.strftime("%H:%M:%S")
            self.status_var.set(f"System stopped - Reason: {reason} at {time_str}")
            logger.info(f"System stopped - Reason: {reason} at {time_str}")
            
            # Send email notification in separate thread
            if 'EMAIL_CONFIG' in config:
                send_email_notification(reason, stop_time)
        
        self.update_stop_button()
        if window:
            window.destroy()
    
    def show_settings(self):
        settings_window = tk.Toplevel(self.root)
        settings_window.title("System Settings")
        settings_window.geometry("500x400")
        settings_window.resizable(False, False)
        settings_window.configure(bg=self.BG_COLOR)
        
        settings_window.grab_set()
        settings_window.focus_set()
        
        # Configure style for entry widgets
        self.style.configure('Settings.TEntry', 
                            fieldbackground='white',  # Background color of the entry field
                            foreground='black',      # Text color
                            insertbackground='black', # Cursor color
                            padding=5)
        
        main_frame = ttk.Frame(settings_window, padding=20)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Server Settings
        server_frame = ttk.LabelFrame(main_frame, text="Server Configuration", padding=1)
        server_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(server_frame, text="Server IP:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        self.ip_entry = ttk.Entry(server_frame, style='Settings.TEntry')
        self.ip_entry.insert(0, SERVER_IP)
        self.ip_entry.grid(row=0, column=1, sticky=tk.EW, padx=5, pady=5)
        
        ttk.Label(server_frame, text="Server Port:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=5)
        self.port_entry = ttk.Entry(server_frame, style='Settings.TEntry')
        self.port_entry.insert(0, str(SERVER_PORT))
        self.port_entry.grid(row=1, column=1, sticky=tk.EW, padx=5, pady=5)
        
        # Machine Settings
        machine_frame = ttk.LabelFrame(main_frame, text="Machine Configuration", padding=1)
        machine_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(machine_frame, text="Machine ID:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        self.id_entry = ttk.Entry(machine_frame, style='Settings.TEntry')
        self.id_entry.insert(0, MACHINE_ID)
        self.id_entry.grid(row=0, column=1, sticky=tk.EW, padx=5, pady=5)
        
        ttk.Label(machine_frame, text="Sampling Interval (s):").grid(row=1, column=0, sticky=tk.W, padx=5, pady=5)
        self.interval_entry = ttk.Entry(machine_frame, style='Settings.TEntry')
        self.interval_entry.insert(0, str(SAMPLING_INTERVAL))
        self.interval_entry.grid(row=1, column=1, sticky=tk.EW, padx=5, pady=5)

        # Simulation Mode
        mode_frame = ttk.LabelFrame(main_frame, text="Operation Mode", padding=1)
        mode_frame.pack(fill=tk.X, pady=5)
        
        self.sim_var = tk.BooleanVar(value=SIMULATION)
        ttk.Checkbutton(mode_frame, text="Simulation Mode", variable=self.sim_var).pack(anchor=tk.W)
        
        # Buttons
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=10)
        
        ttk.Button(button_frame, text="Save", command=self.save_settings, style='Large.TButton').pack(side=tk.RIGHT, padx=1)
        ttk.Button(button_frame, text="Cancel", command=settings_window.destroy, style='Large.TButton').pack(side=tk.RIGHT, padx=1)
        
        server_frame.columnconfigure(1, weight=1)
        machine_frame.columnconfigure(1, weight=1)
    
    def save_settings(self):
        global SERVER_IP, SERVER_PORT, MACHINE_ID, SAMPLING_INTERVAL, SIMULATION
    
        try:
            # Update the config dictionary
            config['SERVER_IP'] = self.ip_entry.get()
            if not config['SERVER_IP']:
                raise ValueError("Server IP cannot be empty")
        
            new_port = int(self.port_entry.get())
            if not (0 < new_port <= 65535):
                raise ValueError("Port must be between 1 and 65535")
            config['SERVER_PORT'] = new_port
        
            new_id = self.id_entry.get()
            if not new_id:
                raise ValueError("Machine ID cannot be empty")
            config['MACHINE_ID'] = new_id
        
            new_interval = float(self.interval_entry.get())
            if new_interval <= 0:
                raise ValueError("Sampling interval must be positive")
            config['SAMPLING_INTERVAL'] = new_interval
        
            config['SIMULATION'] = self.sim_var.get()
        
            # Save to YAML file
            if not save_config(config):
                raise Exception("Failed to save configuration file")
        
            # Update global variables
            SERVER_IP = config['SERVER_IP']
            SERVER_PORT = config['SERVER_PORT']
            MACHINE_ID = config['MACHINE_ID']
            SAMPLING_INTERVAL = config['SAMPLING_INTERVAL']
            SIMULATION = config['SIMULATION']
        
            messagebox.showinfo("Success", "Settings saved successfully")
            logger.info(f"Settings updated - IP: {SERVER_IP}, Port: {SERVER_PORT}, "
                    f"Machine ID: {MACHINE_ID}, Interval: {SAMPLING_INTERVAL}, "
                    f"Simulation: {SIMULATION}")
        
            self.ip_entry.master.master.destroy()
        
        except ValueError as e:
            messagebox.showerror("Invalid Settings", str(e))
            logger.error(f"Failed to save settings: {str(e)}")
        except Exception as e:
            messagebox.showerror("Error", f"An unexpected error occurred: {str(e)}")
            logger.error(f"Unexpected error saving settings: {str(e)}")
    
    def confirm_exit(self):
        if messagebox.askyesno("Exit", "Are you sure you want to exit the application?"):
            save_counters(qtBon, qtRejet)
            stop_event.set()
            time_module.sleep(0.5)
            self.root.destroy()
if __name__ == "__main__":
    logger.info("Starting Production Monitor...")
    logger.info(f"Mode: {'SIMULATION' if SIMULATION else 'HARDWARE'}")
    logger.info(f"Shift schedule: {[s['name'] for s in SHIFT_SCHEDULE]}")
    logger.info(f"Current shift: {get_current_shift()}")
    
    root = tk.Tk()
    app = ProductionMonitor(root)
    
    def data_loop():
        global qtBon, qtRejet
        logger.info("Starting data transmission thread")
        while not stop_event.is_set():
            try:
                data = collect_data()
                logger.debug(f"Collected data: {json.dumps(data)}")
                
                
                save_counters(qtBon, qtRejet)
                if send_data(data):
                    logger.info("Data sent successfully")
                else:
                    logger.warning("Failed to send data")
                
                for _ in range(int(SAMPLING_INTERVAL * 10)):
                    if stop_event.is_set():
                        break
                    time_module.sleep(0.1)
            except Exception as e:
                logger.error(f"Data loop error: {str(e)}")
                if stop_event.is_set():
                    break
                time_module.sleep(1)
    
    data_thread = threading.Thread(target=data_loop, daemon=True)
    data_thread.start()
    
    def on_closing():
        app.confirm_exit()
    
    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()
    
    stop_event.set()
    logger.info("Program stopped")






