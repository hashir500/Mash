import httpx
import json
from datetime import datetime, timedelta
import socket

class ActivityWatchClient:
    def __init__(self, host="127.0.0.1", port=5600):
        self.base_url = f"http://{host}:{port}/api/0"
        self.hostname = socket.gethostname()

    def test_connection(self):
        try:
            r = httpx.get(f"{self.base_url}/buckets", timeout=2.0, follow_redirects=True)
            return r.status_code == 200
        except:
            return False

    def get_window_bucket(self):
        try:
            r = httpx.get(f"{self.base_url}/buckets", follow_redirects=True)
            buckets = r.json()
            for b_id in buckets:
                if "aw-watcher-window" in b_id:
                    return b_id
        except:
            pass
        return None

    def calculate_total_time(self, start, end):
        """Returns total active minutes between start and end datetimes"""
        bucket = self.get_window_bucket()
        if not bucket: return 0
        
        try:
            # Query for events in the time range using local ISO format
            start_iso = start.isoformat()
            end_iso = end.isoformat()
            r = httpx.get(f"{self.base_url}/buckets/{bucket}/events?start={start_iso}&end={end_iso}&limit=-1", follow_redirects=True)
            events = r.json()
            
            total_duration = 0
            for event in events:
                total_duration += event.get("duration", 0)
            
            return int(total_duration / 60) # Convert to minutes
        except:
            return 0

    def calculate_app_usage(self, start, end):
        """Returns {app_name: minutes} for the time range"""
        bucket = self.get_window_bucket()
        if not bucket: return {}
        
        try:
            start_iso = start.isoformat()
            end_iso = end.isoformat()
            r = httpx.get(f"{self.base_url}/buckets/{bucket}/events?start={start_iso}&end={end_iso}&limit=-1", follow_redirects=True)
            events = r.json()
            
            app_usage = {}
            for event in events:
                app = event.get("data", {}).get("app", "Unknown")
                duration = event.get("duration", 0)
                app_usage[app] = app_usage.get(app, 0) + (duration / 60)
            
            # Convert to int minutes
            return {k: int(v) for k, v in app_usage.items() if v > 1}
        except:
            return {}

    def get_activity_timeline(self, start, end, limit=10):
        """Returns list of activity dicts with merged consecutive events"""
        bucket = self.get_window_bucket()
        if not bucket: return []
        
        try:
            start_iso = start.isoformat()
            end_iso = end.isoformat()
            # Get a large enough sample to merge heartbeats
            r = httpx.get(f"{self.base_url}/buckets/{bucket}/events?start={start_iso}&end={end_iso}&limit=1000", follow_redirects=True)
            events = r.json()
            
            merged = []
            for event in events:
                app = event.get("data", {}).get("app", "Unknown")
                title = event.get("data", {}).get("title", "")
                duration = event.get("duration", 0)
                ts = datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00"))
                
                if merged and merged[-1]["title"] == app and merged[-1]["subtitle"] == title:
                    # Same as last event, add duration
                    merged[-1]["raw_duration"] += duration
                else:
                    # New event
                    merged.append({
                        "title": app,
                        "subtitle": title[:50] + "..." if len(title) > 50 else title,
                        "timestamp": ts.strftime("%I:%M %p"),
                        "raw_duration": duration
                    })
            
            # Format durations for top N merged events
            timeline = []
            for m in merged[:limit]:
                dur = m["raw_duration"]
                if dur >= 60:
                    dur_str = f"{int(dur / 60)}m"
                else:
                    dur_str = f"{int(dur)}s"
                
                timeline.append({
                    "title": m["title"],
                    "subtitle": m["subtitle"],
                    "timestamp": m["timestamp"],
                    "duration": dur_str
                })
            return timeline
        except:
            return []

    def get_daily_heatmap_data(self, days=365):
        """Returns {date_str: level} by fetching events in one large batch"""
        bucket = self.get_window_bucket()
        if not bucket: return {}
        
        heatmap = {}
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        
        try:
            # Single large query for all events in the year
            start_iso = start_date.isoformat()
            end_iso = end_date.isoformat()
            r = httpx.get(f"{self.base_url}/buckets/{bucket}/events?start={start_iso}&end={end_iso}&limit=-1", follow_redirects=True, timeout=30.0)
            events = r.json()
            
            # Group duration by date
            daily_minutes = {}
            for event in events:
                # Event timestamp looks like "2024-05-15T..."
                date_str = event["timestamp"].split("T")[0]
                duration = event.get("duration", 0)
                daily_minutes[date_str] = daily_minutes.get(date_str, 0) + (duration / 60)
            
            for date_str, minutes in daily_minutes.items():
                if minutes == 0: level = 0
                elif minutes < 60: level = 1
                elif minutes < 180: level = 2
                elif minutes < 360: level = 3
                else: level = 4
                heatmap[date_str] = level
                
            return heatmap
        except Exception as e:
            print(f"Error fetching bulk heatmap data: {e}")
            return {}

aw_client = ActivityWatchClient()
