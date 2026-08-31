# Centionaire (Android)

Phone app that loads the **same live UI** you already use in the browser, plus native notifications for fills and window results.

The bot still runs on the PC. The phone is a remote screen + notifier.

## What you need

1. The live bot running on the PC.
2. Tailscale on PC **and** phone, same account. Then:

```powershell
cd trader
.\tailscale-ui.ps1
```

3. Restart the bot after pulling so `/api/notify` exists.
4. On the phone, allow notifications when Android asks.

## Build the APK (this PC)

Install [Android Studio](https://developer.android.com/studio), then:

```powershell
cd trader\android
```

Open the `trader/android` folder in Android Studio → **Trust project** → wait for Gradle sync → **Build > Build Bundle(s) / APK(s) > Build APK(s)**.

APK path after a successful build:

`trader/android/app/build/outputs/apk/debug/app-debug.apk`

A copy is also placed on the Desktop as `Centionaire-debug.apk` when built from this repo.

Copy that file to the phone (USB, Drive, or Tailscale) and install it (enable Install unknown apps). Uninstall the previous build first if Android shows two icons.

Command line, if the Android SDK is installed:

```powershell
cd trader\android
.\gradlew.bat assembleDebug
```

## First launch

1. Open **Centionaire**.
2. Tap **Server** and paste the Tailscale URL printed by `tailscale-ui.ps1` (example `https://drelias.tail86f11c.ts.net/`).
3. Save. The live dashboard should appear.
4. A persistent **Centionaire / Live** notification means background polling is on. Fills: **Bought UP** `35.0¢ · $50`. Results: **UP won** `+$90.58`.

The first poll seeds history **without** notifying, so old trades do not spam you.

## Android Auto

The car cannot show the full dashboard (Google does not allow a WebView on Auto). Centionaire still works in the car as:

1. **Alerts** — fills and results appear as Auto messages (style A: Bought UP / UP won).
2. **Centionaire app in Auto** — a two-line status screen (live state + last alert). Open it from the Auto app list after the phone is connected.

Needs the same Tailscale + phone app running. Sideloaded APKs show up once the phone is plugged in / wireless Auto is connected. If the car list is empty, open **Android Auto** on the phone → **Customize launcher** and enable **Centionaire**.

## If the page is blank

- Tailscale must be connected on the phone.
- The PC UI must be up on port 3848 and served (`tailscale serve --bg 3848`).
- URL in Server must be `https://…` with no trailing junk. Pull-to-refresh the dashboard.
