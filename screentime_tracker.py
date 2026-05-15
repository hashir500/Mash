import datetime
import random

class ScreenTimeTracker:
    def __init__(self):
        self.data = {
            "heatmap_data": [],
            "daily_data": {}
        }
    
    def generate_sample_data(self):
        """Generate fake data for UI demonstration."""
        today = datetime.datetime.now()
        for i in range(365):
            date = today - datetime.timedelta(days=i)
            date_str = date.strftime("%Y-%m-%d")
            level = random.randint(0, 4)
            self.data["heatmap_data"].append({"date": date_str, "level": level})
            self.data["daily_data"][date_str] = {
                "total_minutes": random.randint(60, 600),
                "focused_minutes": random.randint(30, 400)
            }

    def format_minutes(self, minutes):
        h = minutes // 60
        m = minutes % 60
        if h > 0:
            return f"{h}h {m}m"
        return f"{m}m"

    def get_today_activities(self, limit=6):
        return [
            {"title": "VS Code", "subtitle": "Coding", "icon": "assets/laptop.svg", "timestamp": "09:00 AM"},
            {"title": "Chrome", "subtitle": "Browsing", "icon": "assets/globe.svg", "timestamp": "10:30 AM"},
            {"title": "Terminal", "subtitle": "Deployment", "icon": "assets/terminal.svg", "timestamp": "11:45 AM"},
            {"title": "Spotify", "subtitle": "Music", "icon": "assets/coffee.svg", "timestamp": "01:15 PM"},
            {"title": "Figma", "subtitle": "Design", "icon": "assets/laptop.svg", "timestamp": "02:45 PM"},
            {"title": "Slack", "subtitle": "Chat", "icon": "assets/globe.svg", "timestamp": "04:00 PM"}
        ][:limit]

    def sync_from_activitywatch(self, days=7):
        return True

    def get_heatmap_data(self, days=365):
        if not self.data["heatmap_data"]:
            self.generate_sample_data()
        return self.data["heatmap_data"]

    def get_today_stats(self):
        return {"total_minutes": 480, "focused_minutes": 320}

    def get_top_apps(self, limit=4):
        return [
            ("VS Code", 120),
            ("Chrome", 90),
            ("Terminal", 60),
            ("Spotify", 45)
        ][:limit]

tracker = ScreenTimeTracker()
