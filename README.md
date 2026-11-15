# Eye Care App

A lightweight Windows helper app that reminds you to take eye breaks during long screen sessions.

## Features

- Tracks active computer usage using Windows idle detection
- Uses audio activity as an extra signal that the user is still engaged
- Lets you choose between a notification-style break and a full-screen blackout reminder
- Includes snooze support for high-focus or urgent tasks
- Saves settings to `settings.json`
- Runs quietly from the system tray

## Install

```powershell
python -m pip install -r requirements.txt
```

## Run

```powershell
python main.py
```

## Startup behavior

This app is intentionally kept lightweight and simple.

To make it start automatically with Windows, use the standard Windows startup method instead of adding startup logic into the app itself:

1. Open Task Manager
2. Go to Startup apps
3. Find the app entry or add a shortcut to the app in the Startup folder if needed
4. Enable it to launch at startup

This keeps the program as a normal helper app instead of a system-like background service.

## Notes

This app is designed for Windows because it uses Win32 APIs and `pycaw` for audio detection.
