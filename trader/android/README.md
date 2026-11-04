# Centionaire (Android)

Phone app that loads the **same live UI** you already use in the browser, plus profit/loss catch-up notifications when you reconnect.

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

Copy that file to the phone (USB, Drive, or Tailscale) and install it (enable Install unknown apps). Install over the previous build to preserve the last successful report checkpoint.

Polymarket market links stay **inside the app**. Back / the edge swipe returns to the dashboard instead of leaving Centionaire.

Command line, if the Android SDK is installed:

```powershell
cd trader\android
.\gradlew.bat assembleDebug
```

## First launch

1. Open **Centionaire**. The dashboard URL is baked in (`https://ip-172-31-28-129.tail86f11c.ts.net`). There is no app toolbar.
2. Allow notifications when Android asks. Pull down on the dashboard to refresh.

The first poll seeds history **without** notifying, so old trades do not spam you.

## Battery-friendly reports (1.8)

- No persistent foreground service, five-second polling, or application-held wake lock.
- For `.ts.net`, Tailscale IPv4, and Tailscale IPv6 server addresses, Android 9+ schedules checks only when a VPN network is available. Older Android versions skip the request when no VPN is available. A VPN connection still needs to reach the configured server.
- A passive VPN callback requests a check when the app process is alive. Android also maintains a persisted periodic job (15-minute interval, subject to its battery/quota rules) after process death or reboot. Reconnect-only notification delivery is best effort, not guaranteed immediately, particularly with a short VPN session or a force-stopped/restricted app.
- Opening the app, refreshing the dashboard, or refreshing Android Auto requests a check. Repeated requests within 30 seconds are coalesced.
- Failed requests retain the checkpoint. Successful checks produce one recap for unseen filled trades and results, even for a single trade. No new trades means no new notification.
- The server's updated `/api/notify?since=...` includes the whole away period instead of only the last 200 log rows. Restart the updated trader server to enable this; older servers still work but retain their history cap.
- Test notification buttons do not start a service or change the real report checkpoint.

Validation: `./gradlew.bat testDebugUnitTest lintDebug assembleDebug`. On a phone, test reboot, Tailscale off/on after several trades, duplicate refresh, denied notifications, unreachable server, and a full night in the background. Confirm the reconnect total matches the dashboard for the reported interval.

The server reconnect tests use temporary logs: from the repository root, set `PYTHONPATH=trader/src` and run `python -m unittest discover -s trader/tests -v` in an environment with the trader UI dependencies installed.

## Android Auto

The car cannot show the full dashboard (Google does not allow a WebView on Auto). Centionaire still works in the car as:

1. **Alerts** — catch-up reports appear as Auto messages with net profit/loss and trade counts.
2. **Centionaire app in Auto** — a two-line status screen (live state + last alert). Open it from the Auto app list after the phone is connected.

Needs Tailscale connected for fresh data; the car displays the last saved report while offline. Sideloaded APKs are hidden until Android Auto allows unknown sources:

1. Install this APK over the existing Centionaire app and open it once.
2. Phone **Settings → Connected devices → Android Auto** (or search “Android Auto”).
3. Scroll to the bottom, tap **Version** about 10 times until developer settings unlock.
4. Overflow menu → **Developer settings** → enable **Unknown sources**.
5. Disconnect and reconnect the car, then **Customize launcher** and enable **Centionaire**.

## If the page is blank

- Tailscale must be connected on the phone.
- The PC UI must be up on port 3848 and served (`tailscale serve --bg 3848`).
- URL in Server must be `https://…` with no trailing junk. Pull-to-refresh the dashboard.
