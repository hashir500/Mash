#!/bin/bash
# Ultra-aggressive Spotify Finder (Fixed Variable Name)
# 1. Look for anything starting with org.mpris.MediaPlayer2.spotify
PNAME=$(dbus-send --session --dest=org.freedesktop.DBus --type=method_call --print-reply /org/freedesktop/DBus org.freedesktop.DBus.ListNames 2>/dev/null | grep -o "org\.mpris\.MediaPlayer2\.spotify[^\"]*")

# 2. If found, get its unique connection ID
if [ ! -z "$PNAME" ]; then
    BUSID=$(dbus-send --session --dest=org.freedesktop.DBus --type=method_call --print-reply /org/freedesktop/DBus org.freedesktop.DBus.GetNameOwner string:"$PNAME" 2>/dev/null | tail -n 1 | grep -o ":1\.[0-9]\+")
    if [ ! -z "$BUSID" ]; then
        PNAME="$BUSID"
    fi
fi

# 3. Last ditch fallback
if [ -z "$PNAME" ]; then
    PNAME=$(dbus-send --session --dest=org.freedesktop.DBus --type=method_call --print-reply /org/freedesktop/DBus org.freedesktop.DBus.ListNames 2>/dev/null | grep -i "spotify" | head -n 1 | tr -d ' ' | tr -d '"')
fi

if [ -z "$PNAME" ]; then
    PNAME="org.mpris.MediaPlayer2.spotify"
fi

gdbus call --session --dest "$PNAME" --object-path /org/mpris/MediaPlayer2 --method org.freedesktop.DBus.Properties.Get org.mpris.MediaPlayer2.Player Metadata 2>&1
