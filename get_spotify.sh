#!/bin/bash
# Get Spotify metadata using gdbus - Fixed ID lookup
PNAME=$(dbus-send --session --dest=org.freedesktop.DBus --type=method_call --print-reply /org/freedesktop/DBus org.freedesktop.DBus.GetNameOwner string:org.mpris.MediaPlayer2.spotify 2>/dev/null | tail -n 1 | grep -o ":1\.[0-9]\+")

if [ -z "$PNAME" ]; then
    PNAME="org.mpris.MediaPlayer2.spotify"
fi

gdbus call --session --dest "$PNAME" --object-path /org/mpris/MediaPlayer2 --method org.freedesktop.DBus.Properties.Get org.mpris.MediaPlayer2.Player Metadata 2>&1
