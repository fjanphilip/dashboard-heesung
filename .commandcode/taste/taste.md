# Taste

- Prefers to be asked clarifying questions when requirements are ambiguous rather than having the assistant guess ("jika bingung tanyakan ke saya"). Confidence: 0.6
- Communicates in Indonesian and expects replies in Indonesian. Confidence: 0.5
- Prefers self-contained Python deployments: embed services (e.g. MQTT broker) in the app process, avoid Docker/Mosquitto/external daemons and heavy dependencies, and include one-click launchers so install on the target PC is simple ("tidak ingin setup ribet"). Confidence: 0.8
- Prefers Arduino IDE over PlatformIO for ESP32/WT32-ETH01 firmware projects; reject build-config files like platformio.ini. Confidence: 0.75
- Prefers firmware that is configured once and then reconfigurable at runtime (e.g. IP/MQTT server stored in NVS via Serial commands) so changing settings never requires re-uploading/compiling. Confidence: 0.65
