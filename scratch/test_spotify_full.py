import subprocess
import re

def test_spotify():
    try:
        # 1. Find the active player name
        find_cmd = "dbus-send --print-reply --dest=org.freedesktop.DBus /org/freedesktop/DBus org.freedesktop.DBus.ListNames"
        names_raw = subprocess.check_output(find_cmd, shell=True).decode()
        players = re.findall(r'string\s+"(org\.mpris\.MediaPlayer2\.[^"]+)"', names_raw)
        
        print(f"DEBUG: Found players: {players}")
        
        if not players:
            print("DEBUG: No players found.")
            return

        # Prioritize Spotify if it exists
        pname = next((p for p in players if "spotify" in p), players[0])
        print(f"DEBUG: Using player: {pname}")
        
        # 2. Get metadata
        cmd = f"dbus-send --print-reply --session --dest={pname} /org/mpris/MediaPlayer2 org.freedesktop.DBus.Properties.Get string:'org.mpris.MediaPlayer2.Player' string:'Metadata'"
        output = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT).decode()
        
        # 3. Robust Regex Parsing
        title_m = re.search(r'xesam:title"\s+variant\s+string\s+"(.*?)"', output, re.DOTALL)
        artist_m = re.search(r'xesam:artist".*?string\s+"(.*?)"', output, re.DOTALL)
        
        if title_m:
            print(f"DEBUG: Song: {title_m.group(1)}")
            if artist_m:
                print(f"DEBUG: Artist: {artist_m.group(1)}")
        else:
            print("DEBUG: No title found in metadata.")
            print(f"DEBUG: Raw output start: {output[:200]}")

    except Exception as e:
        print(f"DEBUG: Error: {e}")

if __name__ == "__main__":
    test_spotify()
