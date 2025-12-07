# Factory Production Monitor with Local Server Integration

A comprehensive industrial monitoring system that collects machine data and 
transmits it to local servers via TCP communication. Perfect for factory 
automation and production line monitoring.

## Key Features
- **Local Server Data Transmission**: TCP socket communication to configured servers
- **Real-time Production Counting**: Track good/reject parts with live counters
- **System Metrics Monitoring**: CPU, Memory, Temperature, Disk, Network usage
- **Touchscreen-Optimized GUI**: Factory-floor friendly interface
- **Automated Email Notifications**: Instant alerts for machine stops
- **Shift-based Operation**: Configurable shift schedules
- **JSON Data Persistence**: Save production counters between sessions
- **Simulation Mode**: Test without physical hardware

## Data Transmission
- Transmits JSON-formatted data via TCP to `SERVER_IP:SERVER_PORT`
- Configurable sampling interval (default: 5 seconds)
- Includes ACK acknowledgment system
- Error counting and retry logic
- Simulation mode for network testing

  
## 🖥️ Hardware Stack
- **Raspberry Pi 4** - Main computing platform
- **Automation HAT** - Hardware I/O for machine interfacing
  - Digital inputs for good/reject part counting
  - Machine state monitoring
  - Relay outputs (optional for control)
- **Raspberry Pi Touch Display 2** - 7" touchscreen interface
  - Factory-floor optimized GUI
  - Touchscreen controls for operators
  - Real-time metrics display
