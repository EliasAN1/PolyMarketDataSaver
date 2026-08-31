# Android watcher (APK)

Phone app that loads the **same trader UI** you already use in the browser, plus native notifications for fills and window results.

The trader still runs on the PC. The phone is a remote screen + notifier.

## What you need

1. Trader running on the PC (`python -m pmtrader` or `--dry-run`).
2. Tailscale on PC **and** phone, same account. Then:

```powershell
cd trader
.\tailscale-ui.ps1
```

3. Restart the trader after pulling so `/api/notify` exists.
4. On the phone, allow notifications when Android asks.

## Build the APK (this PC)

Install [Android Studio](https://developer.android.com/studio), then:

```powershell
cd trader\android
```

Open the `trader/android` folder in Android Studio → **Trust project** → wait for Gradle sync → **Build > Build Bundle(s) / APK(s) > Build APK(s)**.

APK path after a successful build:

`trader/android/app/build/outputs/apk/debug/app-debug.apk`

A copy is also placed on the Desktop as `pmtrader-debug.apk` when built from this repo.

Copy that file to the phone (USB, Drive, or Tailscale) and install it (enable Install unknown apps).

Command line, if the Android SDK is installed:

```powershell
cd trader\android
.\gradlew.bat assembleDebug
```

## First launch

1. Open **pmtrader**.
2. Tap **Server** and paste the Tailscale URL printed by `tailscale-ui.ps1` (example `https://drelias.tail86f11c.ts.net/`).
3. Save. The live dashboard should appear.
4. A persistent **Watching trader…** notification means background polling is on. Trade fills use the **Trades** channel; wins/losses use **Results**.

The first poll seeds history **without** notifying, so old trades do not spam you.

## If the page is blank

- Tailscale must be connected on the phone.
- PC trader UI must be up on port 3848 and served (`tailscale serve --bg 3848`).
- URL in Server must be `https://…` with no trailing junk. Pull-to-refresh the dashboard.
