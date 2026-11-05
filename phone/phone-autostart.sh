# Run this once in Termux (after installing Termux:Boot and opening it once).
# Creates a boot script that starts the dictation server whenever the phone boots,
# and restarts it if it ever crashes.

mkdir -p ~/.termux/boot
cat > ~/.termux/boot/start-dictation-relay <<'EOF'
#!/data/data/com.termux/files/usr/bin/sh
termux-wake-lock
cd ~
while true; do
  python ~/server.py >> ~/relay-server.log 2>&1
  echo "$(date) server exited, restarting in 3s" >> ~/relay-server.log
  sleep 3
done
EOF
chmod +x ~/.termux/boot/start-dictation-relay

# Same thing as a home-screen button (needs the Termux:Widget app, optional).
mkdir -p ~/.shortcuts/tasks   # tasks/ = runs in background, no terminal window
cp ~/.termux/boot/start-dictation-relay ~/.shortcuts/tasks/start-dictation-relay
cat > ~/.shortcuts/tasks/stop-dictation-relay <<'EOF'
#!/data/data/com.termux/files/usr/bin/sh
pkill -f start-dictation-relay
pkill -f "python /data/data/com.termux/files/home/server.py"
termux-wake-unlock
EOF
chmod +x ~/.shortcuts/tasks/*

echo "Done. Start now without rebooting:  nohup ~/.termux/boot/start-dictation-relay >/dev/null 2>&1 &"
