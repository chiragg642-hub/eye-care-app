# Eye Care App

A lightweight Windows tray app for the 20-20-20 eye-break habit: after 20 minutes of active computer use, look away from the screen for 20 seconds.

## Features

- Tracks Windows idle time and counts active use toward the work interval
- Treats active audio as an additional sign of engagement, which can help during video playback
- Shows a notification or a full-screen break reminder with a countdown
- Lets you snooze a reminder
- Saves validated settings beside `main.py`, so launching from a shortcut uses the same settings
- Runs from the Windows system tray

## Install and run

```powershell
python -m pip install -r requirements.txt
python main.py
```

Use the tray menu's **Start** item to begin tracking. The app does not start tracking until you choose Start. The default work interval is 20 minutes and the default break is 20 seconds; adjust both in **Settings** if needed.

## Start with Windows

To launch the app automatically, place a shortcut to `main.py` (or to a packaged app, if you create one) in the Windows Startup folder. After launch, choose **Start** from the tray menu.

## Notes

This app is designed for Windows. It uses Windows idle detection and `pycaw` audio session information. Audio can count as engagement even when no one is looking at the screen, so use the idle threshold or disable audio playback when you want reminders based only on keyboard and mouse activity.
