import json
import os
from datetime import datetime, timedelta
from collections import defaultdict
import random

# Stub for ActivityWatch if not present
try:
    from activitywatch import aw_client
except ImportError:
    class AWStub:
        def test_connection(self): return False
        def get_daily_heatmap_data(self, days=365): return {}
        def calculate_total_time(self, start, end): return 0
        def calculate_app_usage(self, start, end): return {}
        def get_activity_timeline(self, start, end, limit=10): return []
    aw_client = AWStub()


class ScreenTimeTracker:
    def __init__(self, data_file="screentime_data.json"):
        self.data_file = data_file
        self.data = self.load_data()
        
        # Try to sync from ActivityWatch on startup
        synced = self.sync_from_activitywatch(days=7)
        
        # Fallback to sample data if no data exists at all
        if not self.data["heatmap_data"]:
            self.generate_sample_data()
    
    def load_data(self):
        """Load screen time data from JSON file"""
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, 'r') as f:
                    return json.load(f)
            except:
                return self.get_default_data()
        else:
            return self.get_default_data()
    
    def get_default_data(self):
        """Get default data structure"""
        return {
            "daily_data": {},
            "app_usage": {},
            "activity_timeline": [],
            "heatmap_data": {}
        }
    
    def save_data(self):
        """Save data to JSON file"""
        try:
            with open(self.data_file, 'w') as f:
                json.dump(self.data, f, indent=2)
        except Exception as e:
            print(f"Error saving screen time data: {e}")
    
    def get_date_key(self, date=None):
        """Get date key in YYYY-MM-DD format"""
        if date is None:
            date = datetime.now()
        if isinstance(date, str):
            return date
        return date.strftime("%Y-%m-%d")
    
    def record_screen_time(self, date=None, minutes=0):
        """Record daily screen time"""
        date_key = self.get_date_key(date)
        if date_key not in self.data["daily_data"]:
            self.data["daily_data"][date_key] = {
                "total_minutes": 0,
                "focused_minutes": 0,
                "date": date_key
            }
        self.data["daily_data"][date_key]["total_minutes"] += minutes
        self.save_data()
    
    def record_focused_time(self, date=None, minutes=0):
        """Record focused time (deep work sessions)"""
        date_key = self.get_date_key(date)
        if date_key not in self.data["daily_data"]:
            self.data["daily_data"][date_key] = {
                "total_minutes": 0,
                "focused_minutes": 0,
                "date": date_key
            }
        self.data["daily_data"][date_key]["focused_minutes"] += minutes
        self.save_data()
    
    def record_app_usage(self, app_name, minutes=0, date=None):
        """Record app usage"""
        date_key = self.get_date_key(date)
        if date_key not in self.data["app_usage"]:
            self.data["app_usage"][date_key] = {}
        if app_name not in self.data["app_usage"][date_key]:
            self.data["app_usage"][date_key][app_name] = 0
        self.data["app_usage"][date_key][app_name] += minutes
        self.save_data()
    
    def add_activity(self, title, subtitle, icon, timestamp=None, date_key=None, **kwargs):
        """Add activity to timeline"""
        if timestamp is None:
            timestamp = datetime.now().strftime("%I:%M %p")
        
        if date_key is None:
            date_key = self.get_date_key()
            
        activity = {
            "title": title,
            "subtitle": subtitle,
            "icon": icon,
            "timestamp": timestamp,
            "duration": kwargs.get("duration", ""),
            "date": date_key
        }
        
        # Avoid exact duplicates in timeline
        if self.data["activity_timeline"] and self.data["activity_timeline"][0]["title"] == title and \
           self.data["activity_timeline"][0]["timestamp"] == timestamp:
            return

        self.data["activity_timeline"].insert(0, activity)
        # Keep only last 100 activities
        if len(self.data["activity_timeline"]) > 100:
            self.data["activity_timeline"] = self.data["activity_timeline"][:100]
        self.save_data()
    
    def set_heatmap_activity(self, date=None, level=0):
        """Set activity level for heatmap (0-4)"""
        date_key = self.get_date_key(date)
        self.data["heatmap_data"][date_key] = level
        self.save_data()
    
    def get_today_stats(self):
        """Get today's statistics"""
        date_key = self.get_date_key()
        daily = self.data["daily_data"].get(date_key, {
            "total_minutes": 0,
            "focused_minutes": 0
        })
        return daily
    
    def get_today_app_usage(self):
        """Get today's app usage"""
        date_key = self.get_date_key()
        return self.data["app_usage"].get(date_key, {})
    
    def get_activities_for_day(self, date_key=None, limit=10):
        """Get activities for a specific date"""
        if date_key is None:
            date_key = self.get_date_key()
        
        day_activities = [
            a for a in self.data["activity_timeline"]
            if a.get("date") == date_key
        ]
        return day_activities[:limit]
    
    def get_heatmap_data(self, days=365):
        """Get heatmap data for the last N days"""
        heatmap = []
        end_date = datetime.now()
        
        for i in range(days):
            date = end_date - timedelta(days=i)
            date_key = date.strftime("%Y-%m-%d")
            level = self.data["heatmap_data"].get(date_key, 0)
            heatmap.append({
                "date": date_key,
                "level": level
            })
        
        return list(reversed(heatmap))
    
    def get_top_apps(self, date=None, limit=4):
        """Get top apps by usage time"""
        date_key = self.get_date_key(date)
        app_usage = self.data["app_usage"].get(date_key, {})
        
        # Sort by usage time
        sorted_apps = sorted(app_usage.items(), key=lambda x: x[1], reverse=True)
        return sorted_apps[:limit]
    
    def format_minutes(self, minutes):
        """Format minutes to hours and minutes"""
        if minutes < 60:
            return f"{minutes}m"
        hours = minutes // 60
        mins = minutes % 60
        if mins == 0:
            return f"{hours}h"
        return f"{hours}h {mins}m"
    
    def sync_from_activitywatch(self, days: int = 7):
        """Sync data from ActivityWatch for the last N days"""
        # Test if ActivityWatch is running
        if not aw_client.test_connection():
            print("ActivityWatch is not running. Using existing data.")
            return False
        
        print("Syncing data from ActivityWatch...")
        
        # Sync heatmap data
        try:
            heatmap_data = aw_client.get_daily_heatmap_data(days=365)
            self.data["heatmap_data"] = heatmap_data
        except Exception as e:
            print(f"Error syncing heatmap data: {e}")
        
        # Generate sample data if none exists or if data is empty
        if not self.data["heatmap_data"] or all(v == 0 for v in self.data["heatmap_data"].values()):
            self.generate_sample_data()
        
        # Sync daily data for the last N days
        end_date = datetime.now()
        for i in range(days):
            date = end_date - timedelta(days=i)
            start = datetime(date.year, date.month, date.day)
            end = datetime(date.year, date.month, date.day, 23, 59, 59)
            date_key = date.strftime("%Y-%m-%d")
            
            try:
                # Get total time from ActivityWatch
                total_time = aw_client.calculate_total_time(start, end)
                
                # Calculate actual focused time based on app usage
                app_usage = aw_client.calculate_app_usage(start, end)
                focused_apps = ["VS Code", "Code", "Terminal", "Cursor", "Windsurf", "IntelliJ", "PyCharm", "Figma", "Obsidian", "Antigravity", "Mash"]
                focused_time = sum(v for k, v in app_usage.items() if any(fa in k for fa in focused_apps))
                
                if date_key not in self.data["daily_data"]:
                    self.data["daily_data"][date_key] = {}
                
                self.data["daily_data"][date_key]["total_minutes"] = total_time
                self.data["daily_data"][date_key]["focused_minutes"] = int(focused_time)
                self.data["daily_data"][date_key]["date"] = date_key
                
                # Get app usage for this date
                app_usage = aw_client.calculate_app_usage(start, end)
                # Clean app names before saving
                cleaned_usage = {self.clean_app_name(k): v for k, v in app_usage.items()}
                self.data["app_usage"][date_key] = cleaned_usage
                
                # Get activity timeline for this date
                timeline = aw_client.get_activity_timeline(start, end, limit=10)
                # Update activity timeline with new events
                for activity in timeline:
                    self.add_activity(
                        self.clean_app_name(activity["title"]), 
                        activity["subtitle"], 
                        "assets/laptop.svg", 
                        activity["timestamp"],
                        date_key=date_key,
                        duration=activity.get("duration", "")
                    )
            
            except Exception as e:
                print(f"Error syncing data for {date_key}: {e}")
        
        self.save_data()
        print("Sync complete.")
        return True

    def clean_app_name(self, name):
        """Clean up messy app names from ActivityWatch"""
        if not name: return "Unknown"
        
        # Handle long chrome/electron strings
        if "chrome-" in name.lower() and "-" in name:
            if "WhatsApp" in name: return "WhatsApp"
            return "Chrome"
        
        # Common cleanup
        replacements = {
            "google-chrome": "Chrome",
            "code": "VS Code",
            "gnome-terminal-server": "Terminal",
            "nautilus": "Files",
            "spotify": "Spotify",
            "discord": "Discord",
            "slack": "Slack"
        }
        
        lower_name = name.lower()
        for k, v in replacements.items():
            if k in lower_name:
                return v
        
        # Capitalize first letter
        return name.capitalize()
    
    def generate_sample_data(self):
        # Generate heatmap data for the last 52 weeks
        end_date = datetime.now()
        for i in range(364):  # 52 weeks x 7 days
            date = end_date - timedelta(days=i)
            date_key = date.strftime("%Y-%m-%d")
            
            # Generate random activity level (more likely to be active on weekdays)
            if date.weekday() < 5:  # Weekday
                rand = random.random()
                if rand < 0.3:
                    level = 0
                elif rand < 0.5:
                    level = 1
                elif rand < 0.7:
                    level = 2
                elif rand < 0.9:
                    level = 3
                else:
                    level = 4
            else:  # Weekend
                rand = random.random()
                if rand < 0.5:
                    level = 0
                elif rand < 0.7:
                    level = 1
                elif rand < 0.85:
                    level = 2
                elif rand < 0.95:
                    level = 3
                else:
                    level = 4
            
            self.data["heatmap_data"][date_key] = level
        
        # Generate daily screen time data for the last 7 days
        for i in range(7):
            date = end_date - timedelta(days=i)
            date_key = date.strftime("%Y-%m-%d")
            
            total_minutes = random.randint(120, 480)  # 2-8 hours
            focused_minutes = random.randint(30, 180)  # 30min-3 hours
            
            self.data["daily_data"][date_key] = {
                "total_minutes": total_minutes,
                "focused_minutes": focused_minutes,
                "date": date_key
            }
        
        # Generate app usage data for today
        date_key = self.get_date_key()
        apps = [
            ("VS Code", random.randint(60, 180)),
            ("Chrome", random.randint(30, 120)),
            ("Slack", random.randint(15, 45)),
            ("Spotify", random.randint(10, 40))
        ]
        
        self.data["app_usage"][date_key] = dict(apps)
        
        # Generate activity timeline for today
        activities = [
            ("Opened Mash", "Initializing systems", "assets/laptop.svg", "08:32 AM"),
            ("VS Code", "Coding Mash UI", "assets/laptop.svg", "09:15 AM"),
            ("Coffee Break", "Manual entry", "assets/coffee.svg", "10:45 AM"),
            ("Deep Work Session", "Python optimization", "assets/laptop.svg", "11:00 AM"),
            ("Browser", "Researching APIs", "assets/globe.svg", "01:30 PM"),
            ("Git Push", "Commit screen time", "assets/terminal.svg", "02:30 PM")
        ]
        
        for title, subtitle, icon, timestamp in activities:
            self.add_activity(title, subtitle, icon, timestamp)
        
        self.save_data()


# Global tracker instance
tracker = ScreenTimeTracker()
