"""
Tactical Drawing Tab - Flight Path Drawing System
Supports drawing aircraft paths and track management with real-time updates
UPDATED: Advanced path editing with drag-based waypoint manipulation
"""

import sys
import json
import random
import math
import threading
from datetime import datetime
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
                               QLabel, QFrame, QListWidget, QSplitter, QTextEdit,
                               QSpinBox, QDoubleSpinBox, QGroupBox, QFormLayout,
                               QComboBox, QScrollArea, QLineEdit, QMessageBox)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtCore import QUrl, Qt, QObject, Signal, Slot, QTimer, Property
from PySide6.QtGui import QFont
from socket_sender_icd import ICDSocketSender


class DrawingBridge(QObject):
    """Bridge for Python-JavaScript communication"""
    
    # Signals to JavaScript
    enableDrawing = Signal(str)  # "aircraft", "track", "drag_aircraft", "drag_track"
    disableDrawing = Signal()
    clearAllPaths = Signal()
    addPath = Signal(str)  # JSON path data
    updatePosition = Signal(str)  # JSON update
    
    # NEW: Path editing signals
    startPathEditing = Signal(str)  # pathId
    stopPathEditing = Signal()
    pathDeleted = Signal(str)  # pathId
    restorePath = Signal(str)  # JSON path data for restoration after cancel
    
    # Signals from JavaScript
    pathDrawn = Signal(str)  # Emitted when user draws a path
    waypointMoved = Signal(str)  # {pathId, index, lat, lon}
    waypointInserted = Signal(str)  # {pathId, insertIndex, lat, lon}
    waypointDeleted = Signal(str)  # {pathId, index}
    pathEditCompleted = Signal(str)  # Full updated path JSON
    
    def __init__(self):
        super().__init__()
        self._drawing_mode = ""
    
    @Slot(str)
    def onPathDrawn(self, path_json):
        """Called from JavaScript when path is drawn"""
        self.pathDrawn.emit(path_json)
    
    @Slot(str)
    def onWaypointMoved(self, update_json):
        """Called from JavaScript when waypoint is dragged"""
        self.waypointMoved.emit(update_json)
    
    @Slot(str)
    def onWaypointInserted(self, insert_json):
        """Called from JavaScript when waypoint is inserted"""
        self.waypointInserted.emit(insert_json)
    
    @Slot(str)
    def onWaypointDeleted(self, delete_json):
        """Called from JavaScript when waypoint is deleted"""
        self.waypointDeleted.emit(delete_json)
    
    @Slot(str)
    def onPathEditCompleted(self, path_json):
        """Called from JavaScript when editing is saved"""
        self.pathEditCompleted.emit(path_json)
    
    @Property(str, notify=enableDrawing)
    def drawingMode(self):
        return self._drawing_mode


class PathManager:
    """Manages aircraft and track paths with thread-safe operations"""
    
    def __init__(self):
        self.aircraft_paths = {}  # ID -> path data
        self.track_paths = {}     # ID -> path data
        self.next_aircraft_id = 1
        self.next_track_id = 1
        self.lock = threading.Lock()
        
    def add_aircraft_path(self, points, distance, speed=None, altitude=None):
        """Add new aircraft path"""
        with self.lock:
            path_id = f"AC-{self.next_aircraft_id:04d}"
            self.next_aircraft_id += 1
            
            self.aircraft_paths[path_id] = {
                'id': path_id,
                'type': 'aircraft',
                'points': points,
                'distance_nm': distance,
                'speed_kts': speed if speed else random.randint(400, 550),
                'altitude_ft': altitude if altitude else random.randint(10000, 35000),
                'current_position': 0,  # Progress along path (0-1)
                'color': 'green',
                'created': datetime.now().isoformat()
            }
            
            return path_id, self.aircraft_paths[path_id]
    
    def add_track_path(self, points, distance, speed=None, altitude=None):
        """Add new hostile track path"""
        with self.lock:
            path_id = f"TRK-{self.next_track_id:04d}"
            self.next_track_id += 1
            
            self.track_paths[path_id] = {
                'id': path_id,
                'type': 'track',
                'points': points,
                'distance_nm': distance,
                'speed_kts': speed if speed else random.randint(350, 500),
                'altitude_ft': altitude if altitude else random.randint(5000, 20000),
                'current_position': 0,
                'color': 'red',
                'created': datetime.now().isoformat()
            }
            
            return path_id, self.track_paths[path_id]
    
    def get_all_paths(self):
        """Get all paths combined (thread-safe)"""
        with self.lock:
            all_paths = {}
            all_paths.update(self.aircraft_paths)
            all_paths.update(self.track_paths)
            return all_paths.copy()
    
    def get_path_by_id(self, path_id):
        """Get path data by ID (thread-safe)"""
        with self.lock:
            if path_id in self.aircraft_paths:
                return self.aircraft_paths[path_id].copy()
            elif path_id in self.track_paths:
                return self.track_paths[path_id].copy()
            return None
    
    def update_path_points(self, path_id, new_points):
        """Update path points and recalculate distance"""
        with self.lock:
            path = None
            if path_id in self.aircraft_paths:
                path = self.aircraft_paths[path_id]
            elif path_id in self.track_paths:
                path = self.track_paths[path_id]
            
            if path:
                path['points'] = new_points
                path['distance_nm'] = self._calculate_distance(new_points)
                # Keep current position but clamp to valid range
                path['current_position'] = min(path['current_position'], 0.999)
                return True
            return False
    
    def insert_waypoint(self, path_id, insert_index, new_point):
        """Insert waypoint at specific index in path"""
        with self.lock:
            path = None
            if path_id in self.aircraft_paths:
                path = self.aircraft_paths[path_id]
            elif path_id in self.track_paths:
                path = self.track_paths[path_id]
            
            if path and 0 <= insert_index <= len(path['points']):
                path['points'].insert(insert_index, new_point)
                path['distance_nm'] = self._calculate_distance(path['points'])
                return True
            return False
    
    def remove_waypoint(self, path_id, waypoint_index):
        """Remove waypoint from path (minimum 2 points required)"""
        with self.lock:
            path = None
            if path_id in self.aircraft_paths:
                path = self.aircraft_paths[path_id]
            elif path_id in self.track_paths:
                path = self.track_paths[path_id]
            
            if path and len(path['points']) > 2 and 0 <= waypoint_index < len(path['points']):
                path['points'].pop(waypoint_index)
                path['distance_nm'] = self._calculate_distance(path['points'])
                return True
            return False
    
    def delete_path(self, path_id):
        """Delete a complete path"""
        with self.lock:
            if path_id in self.aircraft_paths:
                deleted = self.aircraft_paths.pop(path_id)
                return deleted
            elif path_id in self.track_paths:
                deleted = self.track_paths.pop(path_id)
                return deleted
            return None
    
    def clear_all(self):
        """Clear all paths"""
        with self.lock:
            self.aircraft_paths.clear()
            self.track_paths.clear()
            self.next_aircraft_id = 1
            self.next_track_id = 1
    
    def update_positions(self, delta_time):
        """Update all aircraft positions along their paths (thread-safe)"""
        updates = []
        
        with self.lock:
            for path_id, path in self.aircraft_paths.items():
                # Update position based on speed and time
                speed_nm_per_sec = path['speed_kts'] / 3600
                distance_traveled = speed_nm_per_sec * delta_time
                
                if path['distance_nm'] > 0:
                    position_delta = distance_traveled / path['distance_nm']
                    path['current_position'] += position_delta
                    
                    # Loop back to start
                    if path['current_position'] >= 1.0:
                        path['current_position'] = path['current_position'] % 1.0
                        
                    # Calculate current lat/lon
                    current_point = self._interpolate_path(path['points'], path['current_position'])
                    
                    updates.append({
                        'id': path_id,
                        'type': 'aircraft',
                        'lat': current_point[0],
                        'lon': current_point[1],
                        'alt': path['altitude_ft'],
                        'speed': path['speed_kts'],
                        'heading': self._calculate_heading(path['points'], path['current_position'])
                    })
            
            for path_id, path in self.track_paths.items():
                speed_nm_per_sec = path['speed_kts'] / 3600
                distance_traveled = speed_nm_per_sec * delta_time
                
                if path['distance_nm'] > 0:
                    position_delta = distance_traveled / path['distance_nm']
                    path['current_position'] += position_delta
                    
                    if path['current_position'] >= 1.0:
                        path['current_position'] = path['current_position'] % 1.0
                    
                    current_point = self._interpolate_path(path['points'], path['current_position'])
                    
                    updates.append({
                        'id': path_id,
                        'type': 'track',
                        'lat': current_point[0],
                        'lon': current_point[1],
                        'alt': path['altitude_ft'],
                        'speed': path['speed_kts'],
                        'heading': self._calculate_heading(path['points'], path['current_position'])
                    })
        
        return updates
    
    def _interpolate_path(self, points, position):
        """Interpolate position along path"""
        if len(points) < 2:
            return points[0] if points else [0, 0]
        
        # Find segment
        segment_length = 1.0 / (len(points) - 1)
        segment_index = int(position / segment_length)
        
        if segment_index >= len(points) - 1:
            return points[-1]
        
        # Interpolate within segment
        local_position = (position - segment_index * segment_length) / segment_length
        
        p1 = points[segment_index]
        p2 = points[segment_index + 1]
        
        lat = p1[0] + (p2[0] - p1[0]) * local_position
        lon = p1[1] + (p2[1] - p1[1]) * local_position
        
        return [lat, lon]
    
    def _calculate_heading(self, points, position):
        """Calculate heading at current position"""
        if len(points) < 2:
            return 0
        
        current = self._interpolate_path(points, position)
        next_pos = min(position + 0.01, 0.999)
        next_point = self._interpolate_path(points, next_pos)
        
        dlat = next_point[0] - current[0]
        dlon = next_point[1] - current[1]
        
        if dlat == 0 and dlon == 0:
            return 0
        
        heading = math.degrees(math.atan2(dlon, dlat)) % 360
        return heading
    
    def _calculate_distance(self, points):
        """Calculate total path distance in nautical miles"""
        if len(points) < 2:
            return 0
        
        total_distance = 0
        for i in range(len(points) - 1):
            p1 = points[i]
            p2 = points[i + 1]
            
            R = 3440.065
            lat1, lon1 = math.radians(p1[0]), math.radians(p1[1])
            lat2, lon2 = math.radians(p2[0]), math.radians(p2[1])
            
            dlat = lat2 - lat1
            dlon = lon2 - lon1
            
            a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
            c = 2 * math.asin(math.sqrt(a))
            
            distance = R * c
            total_distance += distance
        
        return total_distance


class TacticalDrawingTab(QWidget):
    """Main tactical drawing tab widget"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # Path manager
        self.path_manager = PathManager()
        
        # ICD Socket sender for real-time data (50Hz transmission rate)
        self.socket_sender = ICDSocketSender(host='127.0.0.1', port=5001)
        self.socket_sender.start()  # Auto-start for real-time COP behavior
        
        # Drawing state
        self.drawing_mode = None  # "aircraft", "track", "drag_aircraft", "drag_track"
        self.default_speed = 450
        self.sending_enabled = True  # Auto-enabled for COP system
        
        # Editing state
        self.editing_path_id = None
        self.original_path_data = None
        
        # Setup UI
        self.setup_ui()
        
        # Update timer (50Hz for smooth updates - synchronized with ICD socket rate)
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.update_aircraft_positions)
        self.update_timer.start(20)  # 20ms = 50Hz (matches ICD transmission rate)
        
        self.last_update_time = datetime.now()
    
    def setup_ui(self):
        """Setup the user interface"""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        
        # Left panel - Controls (wider for better UI)
        left_panel = self.create_left_panel()
        left_panel.setMinimumWidth(400)
        left_panel.setMaximumWidth(500)
        
        # Right panel - Map
        right_panel = self.create_map_panel()
        
        # Splitter
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 1)  # Left panel
        splitter.setStretchFactor(1, 4)  # Map panel (larger)
        
        layout.addWidget(splitter)
    
    def create_left_panel(self):
        """Create left control panel"""
        panel = QFrame()
        panel.setFrameStyle(QFrame.Box)
        panel.setStyleSheet("""
            QFrame {
                background-color: #0a0a0a;
                border: 2px solid #333333;
            }
        """)
        
        # Use scroll area for better layout
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background-color: #0a0a0a; }")
        
        scroll_content = QWidget()
        layout = QVBoxLayout(scroll_content)
        layout.setSpacing(15)
        layout.setContentsMargins(10, 10, 10, 10)
        
        # Title
        title = QLabel("TACTICAL DRAWING CONTROL")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("color: #00ff00; font-size: 14px; font-weight: bold; border: none;")
        layout.addWidget(title)
        
        # Drawing Mode Group
        draw_group = self.create_drawing_mode_group()
        layout.addWidget(draw_group)
        
        # Manual Input Group
        manual_group = self.create_manual_input_group()
        layout.addWidget(manual_group)
        
        # Path Editing Group (NEW)
        edit_group = self.create_path_editing_group()
        layout.addWidget(edit_group)
        
        # Actions Group
        actions_group = self.create_actions_group()
        layout.addWidget(actions_group)
        
        # Path List
        list_group = self.create_path_list_group()
        layout.addWidget(list_group)
        
        # Stats
        self.stats_label = QLabel("Aircraft: 0 | Tracks: 0")
        self.stats_label.setStyleSheet("color: #00ff00; border: none; font-size: 12px;")
        self.stats_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.stats_label)
        
        scroll.setWidget(scroll_content)
        
        main_layout = QVBoxLayout(panel)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll)
        
        return panel
    
    def create_drawing_mode_group(self):
        """Create drawing mode group"""
        group = QGroupBox("DRAWING MODE")
        group.setStyleSheet("QGroupBox { color: #00ff00; font-weight: bold; }")
        layout = QVBoxLayout()
        
        self.btn_aircraft = QPushButton("✈ DRAW AIRCRAFT (Green)")
        self.btn_aircraft.clicked.connect(lambda: self.set_drawing_mode("aircraft"))
        self.btn_aircraft.setStyleSheet(self.get_button_style("#00ff00"))
        layout.addWidget(self.btn_aircraft)
        
        self.btn_track = QPushButton("🎯 DRAW TRACK (Red)")
        self.btn_track.clicked.connect(lambda: self.set_drawing_mode("track"))
        self.btn_track.setStyleSheet(self.get_button_style("#ff0000"))
        layout.addWidget(self.btn_track)
        
        # NEW: Separate drag buttons for aircraft and track
        self.btn_drag_aircraft = QPushButton("✏ DRAG AIRCRAFT (Green)")
        self.btn_drag_aircraft.clicked.connect(lambda: self.set_drawing_mode("drag_aircraft"))
        self.btn_drag_aircraft.setStyleSheet(self.get_button_style("#00ff00"))
        layout.addWidget(self.btn_drag_aircraft)
        
        self.btn_drag_track = QPushButton("✏ DRAG TRACK (Red)")
        self.btn_drag_track.clicked.connect(lambda: self.set_drawing_mode("drag_track"))
        self.btn_drag_track.setStyleSheet(self.get_button_style("#ff0000"))
        layout.addWidget(self.btn_drag_track)
        
        self.btn_stop_draw = QPushButton("⬜ STOP DRAWING")
        self.btn_stop_draw.clicked.connect(self.stop_drawing)
        layout.addWidget(self.btn_stop_draw)
        
        group.setLayout(layout)
        return group
    
    def create_manual_input_group(self):
        """Create manual input group for path creation"""
        group = QGroupBox("MANUAL PATH INPUT")
        group.setStyleSheet("QGroupBox { color: #00ff00; font-weight: bold; }")
        layout = QFormLayout()
        layout.setSpacing(8)
        
        # Type selection
        self.input_type = QComboBox()
        self.input_type.addItems(["Aircraft", "Track"])
        self.input_type.setStyleSheet("background-color: #2a2a2a; color: #00ff00; border: 1px solid #00ff00;")
        layout.addRow("Type:", self.input_type)
        
        # Speed input
        self.input_speed = QSpinBox()
        self.input_speed.setRange(100, 1500)
        self.input_speed.setValue(450)
        self.input_speed.setSuffix(" kts")
        self.input_speed.setStyleSheet("background-color: #2a2a2a; color: #00ff00;")
        layout.addRow("Speed:", self.input_speed)
        
        # Altitude input
        self.input_altitude = QSpinBox()
        self.input_altitude.setRange(1000, 50000)
        self.input_altitude.setValue(15000)
        self.input_altitude.setSuffix(" ft")
        self.input_altitude.setStyleSheet("background-color: #2a2a2a; color: #00ff00;")
        layout.addRow("Altitude:", self.input_altitude)
        
        # Start Latitude
        self.input_start_lat = QDoubleSpinBox()
        self.input_start_lat.setRange(-90, 90)
        self.input_start_lat.setValue(20.5937)
        self.input_start_lat.setDecimals(4)
        self.input_start_lat.setStyleSheet("background-color: #2a2a2a; color: #00ff00;")
        layout.addRow("Start Lat:", self.input_start_lat)
        
        # Start Longitude
        self.input_start_lon = QDoubleSpinBox()
        self.input_start_lon.setRange(-180, 180)
        self.input_start_lon.setValue(78.9629)
        self.input_start_lon.setDecimals(4)
        self.input_start_lon.setStyleSheet("background-color: #2a2a2a; color: #00ff00;")
        layout.addRow("Start Lon:", self.input_start_lon)
        
        # End Latitude
        self.input_end_lat = QDoubleSpinBox()
        self.input_end_lat.setRange(-90, 90)
        self.input_end_lat.setValue(28.6139)
        self.input_end_lat.setDecimals(4)
        self.input_end_lat.setStyleSheet("background-color: #2a2a2a; color: #00ff00;")
        layout.addRow("End Lat:", self.input_end_lat)
        
        # End Longitude
        self.input_end_lon = QDoubleSpinBox()
        self.input_end_lon.setRange(-180, 180)
        self.input_end_lon.setValue(77.2090)
        self.input_end_lon.setDecimals(4)
        self.input_end_lon.setStyleSheet("background-color: #2a2a2a; color: #00ff00;")
        layout.addRow("End Lon:", self.input_end_lon)
        
        # Create button
        btn_create = QPushButton("CREATE PATH")
        btn_create.clicked.connect(self.create_manual_path)
        btn_create.setStyleSheet(self.get_button_style("#00ff00"))
        layout.addRow("", btn_create)
        
        group.setLayout(layout)
        return group
    
    def create_path_editing_group(self):
        """Create path editing control group (NEW)"""
        group = QGroupBox("PATH EDITING")
        group.setStyleSheet("QGroupBox { color: #00ff00; font-weight: bold; }")
        layout = QVBoxLayout()
        
        self.btn_edit_path = QPushButton("✎ EDIT SELECTED PATH")
        self.btn_edit_path.clicked.connect(self.start_path_editing)
        self.btn_edit_path.setEnabled(False)
        self.btn_edit_path.setStyleSheet(self.get_button_style("#00aaff"))
        layout.addWidget(self.btn_edit_path)
        
        self.btn_save_edit = QPushButton("💾 SAVE CHANGES")
        self.btn_save_edit.clicked.connect(self.save_path_edits)
        self.btn_save_edit.setVisible(False)
        self.btn_save_edit.setStyleSheet(self.get_button_style("#00ff00"))
        layout.addWidget(self.btn_save_edit)
        
        self.btn_cancel_edit = QPushButton("❌ CANCEL EDIT")
        self.btn_cancel_edit.clicked.connect(self.cancel_path_editing)
        self.btn_cancel_edit.setVisible(False)
        self.btn_cancel_edit.setStyleSheet(self.get_button_style("#ff0000"))
        layout.addWidget(self.btn_cancel_edit)
        
        self.btn_delete_path = QPushButton("🗑 DELETE SELECTED PATH")
        self.btn_delete_path.clicked.connect(self.delete_selected_path)
        self.btn_delete_path.setEnabled(False)
        self.btn_delete_path.setStyleSheet(self.get_button_style("#ff0000"))
        layout.addWidget(self.btn_delete_path)
        
        group.setLayout(layout)
        return group
    
    def create_actions_group(self):
        """Create actions group"""
        group = QGroupBox("ACTIONS")
        group.setStyleSheet("QGroupBox { color: #00ff00; font-weight: bold; }")
        layout = QVBoxLayout()
        
        btn_random = QPushButton("🎲 GENERATE RANDOM PATHS")
        btn_random.clicked.connect(self.generate_random_paths)
        layout.addWidget(btn_random)
        
        btn_clear = QPushButton("🗑 CLEAR ALL")
        btn_clear.clicked.connect(self.clear_all_paths)
        layout.addWidget(btn_clear)
        
        group.setLayout(layout)
        return group
    
    def create_path_list_group(self):
        """Create path list group"""
        group = QGroupBox("ACTIVE PATHS")
        group.setStyleSheet("QGroupBox { color: #00ff00; font-weight: bold; }")
        layout = QVBoxLayout()
        
        self.path_list = QListWidget()
        self.path_list.setStyleSheet("""
            QListWidget {
                background-color: #000000;
                color: #00ff00;
                border: 1px solid #333333;
                font-family: 'Courier New';
                font-size: 10px;
            }
        """)
        self.path_list.setMaximumHeight(200)
        self.path_list.itemSelectionChanged.connect(self.on_path_selection_changed)
        layout.addWidget(self.path_list)
        
        group.setLayout(layout)
        return group
    
    def create_map_panel(self):
        """Create map display panel"""
        panel = QFrame()
        panel.setFrameStyle(QFrame.Box)
        
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Map view
        self.map_view = QWebEngineView()
        
        # Setup WebChannel for communication
        self.bridge = DrawingBridge()
        self.bridge.pathDrawn.connect(self.on_path_drawn)
        self.bridge.waypointMoved.connect(self.on_waypoint_moved)
        self.bridge.waypointInserted.connect(self.on_waypoint_inserted)
        self.bridge.waypointDeleted.connect(self.on_waypoint_deleted)
        self.bridge.pathEditCompleted.connect(self.on_path_edit_completed)
        
        self.channel = QWebChannel()
        self.channel.registerObject("drawingBridge", self.bridge)
        self.map_view.page().setWebChannel(self.channel)
        
        # Load map HTML
        self.map_view.setUrl(QUrl("http://127.0.0.1:5000/drawing"))
        
        layout.addWidget(self.map_view)
        
        return panel
    
    def get_button_style(self, color):
        """Get button style with color"""
        return f"""
            QPushButton {{
                background-color: #2a2a2a;
                color: {color};
                border: 2px solid {color};
                border-radius: 5px;
                padding: 10px;
                font-weight: bold;
                font-size: 11px;
            }}
            QPushButton:hover {{
                background-color: #3a3a3a;
            }}
            QPushButton:pressed {{
                background-color: {color};
                color: #000000;
            }}
            QPushButton:disabled {{
                background-color: #1a1a1a;
                color: #555555;
                border-color: #555555;
            }}
        """
    
    def set_drawing_mode(self, mode):
        """Set drawing mode"""
        self.drawing_mode = mode
        self.bridge.enableDrawing.emit(mode)
        
        # Reset button styles
        self.btn_aircraft.setStyleSheet(self.get_button_style("#00ff00"))
        self.btn_track.setStyleSheet(self.get_button_style("#ff0000"))
        self.btn_drag_aircraft.setStyleSheet(self.get_button_style("#00ff00"))
        self.btn_drag_track.setStyleSheet(self.get_button_style("#ff0000"))
        
        # Highlight active button
        if mode == "aircraft":
            self.btn_aircraft.setStyleSheet(self.get_button_style("#00ff00") + "font-size: 12px; font-weight: bold;")
        elif mode == "track":
            self.btn_track.setStyleSheet(self.get_button_style("#ff0000") + "font-size: 12px; font-weight: bold;")
        elif mode == "drag_aircraft":
            self.btn_drag_aircraft.setStyleSheet(self.get_button_style("#00ff00") + "font-size: 12px; font-weight: bold;")
        elif mode == "drag_track":
            self.btn_drag_track.setStyleSheet(self.get_button_style("#ff0000") + "font-size: 12px; font-weight: bold;")
    
    def stop_drawing(self):
        """Stop drawing mode"""
        self.drawing_mode = None
        self.bridge.disableDrawing.emit()
        
        self.btn_aircraft.setStyleSheet(self.get_button_style("#00ff00"))
        self.btn_track.setStyleSheet(self.get_button_style("#ff0000"))
        self.btn_drag_aircraft.setStyleSheet(self.get_button_style("#00ff00"))
        self.btn_drag_track.setStyleSheet(self.get_button_style("#ff0000"))
    
    def on_path_selection_changed(self):
        """Handle path selection change (NEW)"""
        has_selection = bool(self.path_list.selectedItems())
        self.btn_edit_path.setEnabled(has_selection and self.editing_path_id is None)
        self.btn_delete_path.setEnabled(has_selection and self.editing_path_id is None)
    
    def start_path_editing(self):
        """Initiate editing mode for selected path (NEW)"""
        selected_items = self.path_list.selectedItems()
        if not selected_items:
            return
        
        # Extract path ID
        item_text = selected_items[0].text()
        path_id = item_text.split('|')[0].strip()
        
        # Get path data
        path_data = self.path_manager.get_path_by_id(path_id)
        if not path_data:
            QMessageBox.warning(self, "Error", "Path not found.")
            return
        
        # Save original data for cancel - DEEP COPY of points
        self.editing_path_id = path_id
        self.original_path_data = {
            'id': path_data['id'],
            'type': path_data['type'],
            'points': [p[:] for p in path_data['points']],  # Deep copy points
            'distance_nm': path_data['distance_nm'],
            'speed_kts': path_data['speed_kts'],
            'altitude_ft': path_data['altitude_ft'],
            'current_position': path_data.get('current_position', 0),
            'color': path_data.get('color', 'green'),
            'created': path_data.get('created', '')
        }
        
        print(f"[START EDIT] Saved original for {path_id}: {len(self.original_path_data['points'])} points")
        
        # Update UI
        self.btn_edit_path.setEnabled(False)
        self.btn_delete_path.setEnabled(False)
        self.btn_save_edit.setVisible(True)
        self.btn_cancel_edit.setVisible(True)
        
        # Disable drawing controls during editing
        self.btn_aircraft.setEnabled(False)
        self.btn_track.setEnabled(False)
        self.btn_drag_aircraft.setEnabled(False)
        self.btn_drag_track.setEnabled(False)
        
        # Emit signal to JavaScript
        self.bridge.startPathEditing.emit(path_id)
    
    '''
    def save_path_edits(self):
        """Finalize and save path edits (NEW)"""
        if not self.editing_path_id:
            return
        
        # Get updated path data from PathManager
        path_data = self.path_manager.get_path_by_id(self.editing_path_id)
        if not path_data:
            self.cancel_path_editing()
            return
        
        print(f"[SAVE EDIT] Saving changes for {self.editing_path_id}: {len(path_data['points'])} points")
        
        # Update list display
        for i in range(self.path_list.count()):
            item = self.path_list.item(i)
            if item.text().startswith(self.editing_path_id):
                item.setText(
                    f"{self.editing_path_id} | {path_data['distance_nm']:.1f}nm | "
                    f"{path_data['speed_kts']}kts | {path_data['altitude_ft']}ft"
                )
                break
        
        # Emit signal to JavaScript to exit editing mode
        self.bridge.stopPathEditing.emit()
        
        # Reset editing state
        self.editing_path_id = None
        self.original_path_data = None
        
        # Restore UI
        self.btn_save_edit.setVisible(False)
        self.btn_cancel_edit.setVisible(False)
        self.btn_aircraft.setEnabled(True)
        self.btn_track.setEnabled(True)
        self.btn_drag_aircraft.setEnabled(True)
        self.btn_drag_track.setEnabled(True)
        
        self.on_path_selection_changed()
        
        QMessageBox.information(self, "Success", "Path changes saved successfully!")
    '''
    def save_path_edits(self):
        """Finalize and save path edits (FIXED)"""
        if not self.editing_path_id:
            return
        
        # Get updated path data from PathManager
        path_data = self.path_manager.get_path_by_id(self.editing_path_id)
        if not path_data:
            self.cancel_path_editing()
            return
        
        print(f"[SAVE EDIT] Saving changes for {self.editing_path_id}: {len(path_data['points'])} points")
        
        # Update list display
        for i in range(self.path_list.count()):
            item = self.path_list.item(i)
            if item.text().startswith(self.editing_path_id):
                item.setText(
                    f"{self.editing_path_id} | {path_data['distance_nm']:.1f}nm | "
                    f"{path_data['speed_kts']}kts | {path_data['altitude_ft']}ft"
                )
                break
            
        # ===== CRITICAL FIX START =====
        # Step 1: Stop editing mode (clears editing layer)
        self.bridge.stopPathEditing.emit()
        
        # Step 2: Re-add the updated path to the map (makes it visible again)
        path_data_json = json.dumps(path_data)
        self.bridge.addPath.emit(path_data_json)
        # ===== CRITICAL FIX END =====
        
        print(f"[SAVE EDIT] Path re-added to map with updated geometry")
        
        # Reset editing state
        self.editing_path_id = None
        self.original_path_data = None
        
        # Restore UI
        self.btn_save_edit.setVisible(False)
        self.btn_cancel_edit.setVisible(False)
        self.btn_aircraft.setEnabled(True)
        self.btn_track.setEnabled(True)
        self.btn_drag_aircraft.setEnabled(True)
        self.btn_drag_track.setEnabled(True)
        
        self.on_path_selection_changed()
        
        QMessageBox.information(self, "Success", "Path changes saved successfully!")

    


    def cancel_path_editing(self):
        """Cancel editing and revert ALL changes (COMPLETELY FIXED)"""
        if not self.editing_path_id or not self.original_path_data:
            return
        
        path_id = self.editing_path_id
        original = self.original_path_data
        
        print(f"[CANCEL EDIT] Reverting {path_id} to original: {len(original['points'])} points")
        
        # Step 1: Restore EVERYTHING in PathManager
        with self.path_manager.lock:
            if path_id in self.path_manager.aircraft_paths:
                self.path_manager.aircraft_paths[path_id]['points'] = [p[:] for p in original['points']]
                self.path_manager.aircraft_paths[path_id]['distance_nm'] = original['distance_nm']
                self.path_manager.aircraft_paths[path_id]['speed_kts'] = original['speed_kts']
                self.path_manager.aircraft_paths[path_id]['altitude_ft'] = original['altitude_ft']
                self.path_manager.aircraft_paths[path_id]['current_position'] = original['current_position']
            elif path_id in self.path_manager.track_paths:
                self.path_manager.track_paths[path_id]['points'] = [p[:] for p in original['points']]
                self.path_manager.track_paths[path_id]['distance_nm'] = original['distance_nm']
                self.path_manager.track_paths[path_id]['speed_kts'] = original['speed_kts']
                self.path_manager.track_paths[path_id]['altitude_ft'] = original['altitude_ft']
                self.path_manager.track_paths[path_id]['current_position'] = original['current_position']
        
        # Step 2: Signal JavaScript to STOP editing and clear all editing artifacts
        self.bridge.stopPathEditing.emit()
        
        # Step 3: Send RESTORE signal with original data to JavaScript
        self.bridge.restorePath.emit(json.dumps(original))
        
        # Reset editing state
        self.editing_path_id = None
        self.original_path_data = None
        
        # Restore UI
        self.btn_save_edit.setVisible(False)
        self.btn_cancel_edit.setVisible(False)
        self.btn_aircraft.setEnabled(True)
        self.btn_track.setEnabled(True)
        self.btn_drag_aircraft.setEnabled(True)
        self.btn_drag_track.setEnabled(True)
        
        self.on_path_selection_changed()
        
        print(f"[CANCEL EDIT] Completed restoration for {path_id}")
    
    def delete_selected_path(self):
        """Delete the selected path (COMPLETELY FIXED)"""
        selected_items = self.path_list.selectedItems()
        if not selected_items:
            return
        
        # Extract path ID
        item_text = selected_items[0].text()
        path_id = item_text.split('|')[0].strip()
        
        # Confirm deletion
        reply = QMessageBox.question(
            self,
            "Confirm Deletion",
            f"Are you sure you want to delete path {path_id}?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return
        
        print(f"[DELETE] Starting deletion for {path_id}")
        
        # Step 1: Delete from PathManager
        deleted = self.path_manager.delete_path(path_id)
        if not deleted:
            print(f"[DELETE] Failed - path not found in PathManager")
            QMessageBox.warning(self, "Error", "Failed to delete path from data.")
            return
        
        print(f"[DELETE] Removed from PathManager: {deleted['type']}")
        
        # Step 2: Remove from list widget
        for i in range(self.path_list.count()):
            item = self.path_list.item(i)
            if item and item.text().startswith(path_id):
                self.path_list.takeItem(i)
                print(f"[DELETE] Removed from UI list at index {i}")
                break
        
        # Step 3: Signal JavaScript to remove from map
        self.bridge.pathDeleted.emit(path_id)
        print(f"[DELETE] Emitted pathDeleted signal")
        
        # Step 4: Update stats
        self.update_stats()
        
        # Step 5: Clear selection
        self.path_list.clearSelection()
        
        print(f"[DELETE] Completed for {path_id}")
        QMessageBox.information(self, "Success", f"Path {path_id} deleted successfully!")
    
    def on_waypoint_moved(self, update_json):
        """Handle waypoint position update during drag (NEW)"""
        try:
            data = json.loads(update_json)
            path_id = data['pathId']
            index = data['index']
            lat = data['lat']
            lon = data['lon']
            
            # Update ONLY during active editing
            if path_id == self.editing_path_id:
                with self.path_manager.lock:
                    if path_id in self.path_manager.aircraft_paths:
                        path = self.path_manager.aircraft_paths[path_id]
                        if 0 <= index < len(path['points']):
                            path['points'][index] = [lat, lon]
                            path['distance_nm'] = self.path_manager._calculate_distance(path['points'])
                    elif path_id in self.path_manager.track_paths:
                        path = self.path_manager.track_paths[path_id]
                        if 0 <= index < len(path['points']):
                            path['points'][index] = [lat, lon]
                            path['distance_nm'] = self.path_manager._calculate_distance(path['points'])
                
        except Exception as e:
            print(f"Error handling waypoint move: {e}")
    
    def on_waypoint_inserted(self, insert_json):
        """Handle new waypoint insertion (NEW)"""
        try:
            data = json.loads(insert_json)
            path_id = data['pathId']
            insert_index = data['insertIndex']
            lat = data['lat']
            lon = data['lon']
            
            # Insert ONLY during active editing
            if path_id == self.editing_path_id:
                self.path_manager.insert_waypoint(path_id, insert_index, [lat, lon])
            
        except Exception as e:
            print(f"Error handling waypoint insertion: {e}")
    
    def on_waypoint_deleted(self, delete_json):
        """Handle waypoint deletion (NEW)"""
        try:
            data = json.loads(delete_json)
            path_id = data['pathId']
            index = data['index']
            
            # Delete ONLY during active editing
            if path_id == self.editing_path_id:
                success = self.path_manager.remove_waypoint(path_id, index)
                if not success:
                    QMessageBox.warning(self, "Error", "Cannot delete waypoint. Minimum 2 waypoints required.")
                
        except Exception as e:
            print(f"Error handling waypoint deletion: {e}")
    
    def on_path_edit_completed(self, path_json):
        """Handle path edit completion from JavaScript (NEW)"""
        try:
            data = json.loads(path_json)
            path_id = data['id']
            new_points = data['points']
            
            # Update path in PathManager
            self.path_manager.update_path_points(path_id, new_points)
            
        except Exception as e:
            print(f"Error handling path edit completion: {e}")
    
    def create_manual_path(self):
        """Create path from manual inputs"""
        path_type = "aircraft" if self.input_type.currentText() == "Aircraft" else "track"
        speed = self.input_speed.value()
        altitude = self.input_altitude.value()
        
        start_lat = self.input_start_lat.value()
        start_lon = self.input_start_lon.value()
        end_lat = self.input_end_lat.value()
        end_lon = self.input_end_lon.value()
        
        # Create path points (straight line with 10 interpolated points)
        points = []
        num_points = 10
        for i in range(num_points):
            t = i / (num_points - 1)
            lat = start_lat + (end_lat - start_lat) * t
            lon = start_lon + (end_lon - start_lon) * t
            points.append([lat, lon])
        
        # Calculate distance
        distance = self.calculate_path_distance(points)
        
        # Add path
        if path_type == "aircraft":
            path_id, path_data = self.path_manager.add_aircraft_path(points, distance, speed, altitude)
        else:
            path_id, path_data = self.path_manager.add_track_path(points, distance, speed, altitude)
        
        # Add to UI
        self.path_list.addItem(
            f"{path_id} | {distance:.1f}nm | {speed}kts | {altitude}ft"
        )
        
        # Send to map
        path_data_json = json.dumps(path_data)
        self.bridge.addPath.emit(path_data_json)
        
        self.update_stats()
    
    def on_path_drawn(self, path_json):
        """Handle path drawn on map"""
        try:
            data = json.loads(path_json)
            points = data['points']
            distance = data['distance_nm']
            
            # Determine type based on drawing mode
            if self.drawing_mode in ["aircraft", "drag_aircraft"]:
                path_id, path_data = self.path_manager.add_aircraft_path(points, distance)
                color = "green"
            elif self.drawing_mode in ["track", "drag_track"]:
                path_id, path_data = self.path_manager.add_track_path(points, distance)
                color = "red"
            else:
                return
            
            # Add to list
            self.path_list.addItem(
                f"{path_id} | {distance:.1f}nm | {path_data['speed_kts']}kts | {path_data['altitude_ft']}ft"
            )
            
            # Send path to map
            path_data_json = json.dumps(path_data)
            self.bridge.addPath.emit(path_data_json)
            
            self.update_stats()
            self.stop_drawing()
            
        except Exception as e:
            print(f"Error processing path: {e}")
    
    def generate_random_paths(self):
        """Generate random aircraft and track paths using threading"""
        def generate():
            # Define multiple regions across India for better distribution
            regions = [
                # North India
                {'min_lat': 28.0, 'max_lat': 35.0, 'min_lon': 74.0, 'max_lon': 80.0},
                # Central India
                {'min_lat': 20.0, 'max_lat': 26.0, 'min_lon': 75.0, 'max_lon': 82.0},
                # South India
                {'min_lat': 8.0, 'max_lat': 16.0, 'min_lon': 75.0, 'max_lon': 80.0},
                # East India
                {'min_lat': 20.0, 'max_lat': 27.0, 'min_lon': 82.0, 'max_lon': 92.0},
                # West India
                {'min_lat': 15.0, 'max_lat': 25.0, 'min_lon': 68.0, 'max_lon': 75.0},
                # Northeast India
                {'min_lat': 23.0, 'max_lat': 29.0, 'min_lon': 88.0, 'max_lon': 96.0},
            ]
            
            # Generate 50 aircraft spread across regions
            for i in range(50):
                region = random.choice(regions)
                points = self.generate_random_points_in_bounds(region, 3, 6)
                distance = self.calculate_path_distance(points)
                
                # Random speed variation
                speed = random.randint(350, 550)
                altitude = random.randint(15000, 35000)
                
                path_id, path_data = self.path_manager.add_aircraft_path(points, distance, speed, altitude)
                
                # Update UI in main thread
                self.path_list.addItem(
                    f"{path_id} | {distance:.1f}nm | {speed}kts | {altitude}ft"
                )
                
                path_data_json = json.dumps(path_data)
                self.bridge.addPath.emit(path_data_json)
            
            # Generate 100 tracks spread across regions
            for i in range(100):
                region = random.choice(regions)
                points = self.generate_random_points_in_bounds(region, 3, 6)
                distance = self.calculate_path_distance(points)
                
                # Random speed variation
                speed = random.randint(300, 500)
                altitude = random.randint(5000, 25000)
                
                path_id, path_data = self.path_manager.add_track_path(points, distance, speed, altitude)
                
                self.path_list.addItem(
                    f"{path_id} | {distance:.1f}nm | {speed}kts | {altitude}ft"
                )
                
                path_data_json = json.dumps(path_data)
                self.bridge.addPath.emit(path_data_json)
            
            self.update_stats()
        
        # Run in separate thread
        thread = threading.Thread(target=generate, daemon=True)
        thread.start()
    
    def generate_random_points_in_bounds(self, bounds, min_points, max_points):
        """Generate random path points within geographic bounds"""
        num_points = random.randint(min_points, max_points)
        points = []
        
        for i in range(num_points):
            lat = random.uniform(bounds['min_lat'], bounds['max_lat'])
            lon = random.uniform(bounds['min_lon'], bounds['max_lon'])
            points.append([lat, lon])
        
        return points
    
    def calculate_path_distance(self, points):
        """Calculate total path distance in nautical miles"""
        if len(points) < 2:
            return 0
        
        total_distance = 0
        for i in range(len(points) - 1):
            p1 = points[i]
            p2 = points[i + 1]
            
            R = 3440.065
            lat1, lon1 = math.radians(p1[0]), math.radians(p1[1])
            lat2, lon2 = math.radians(p2[0]), math.radians(p2[1])
            
            dlat = lat2 - lat1
            dlon = lon2 - lon1
            
            a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
            c = 2 * math.asin(math.sqrt(a))
            
            distance = R * c
            total_distance += distance
        
        return total_distance
    
    def clear_all_paths(self):
        """Clear all paths"""
        self.path_manager.clear_all()
        self.path_list.clear()
        self.bridge.clearAllPaths.emit()
        self.update_stats()
    
    def update_stats(self):
        """Update statistics display"""
        num_aircraft = len(self.path_manager.aircraft_paths)
        num_tracks = len(self.path_manager.track_paths)
        self.stats_label.setText(f"Aircraft: {num_aircraft} | Tracks: {num_tracks}")
    
    def update_aircraft_positions(self):
        """Update all aircraft positions (called at 50Hz)"""
        current_time = datetime.now()
        delta_time = (current_time - self.last_update_time).total_seconds()
        self.last_update_time = current_time
        
        # Get position updates
        updates = self.path_manager.update_positions(delta_time)
        
        if updates:
            # Send batch update to map
            updates_json = json.dumps(updates)
            self.bridge.updatePosition.emit(updates_json)
            
            # Send to ICD socket if enabled
            if self.sending_enabled and updates:
                self.socket_sender.update_data(updates)
