# ProfSchedule AI — mobile

## Why Capacitor

The web app is server-rendered Jinja: 12 templates, 21 classic scripts sharing
globals, no bundler and no build step. That fact decided the framework.

| Option | Verdict |
|---|---|
| **React Native / Expo** | Rejected. Every template and script would have to be rewritten as components — a full frontend rewrite of a working app. |
| **Capacitor, bundled web assets** | Impossible. There is nothing static to bundle; every page is rendered per request from session state. |
| **Capacitor, remote URL + native plugins** | **Chosen.** Zero change to the existing frontend, real native APIs on top. |
| **TWA** | Kept as the lighter Android-only alternative — see `mobile/twa/`. |

### Be clear about what this is

Because the app is server-rendered, the shell loads the live site. Any wrapper
would. What separates this from a bad WebView is the native layer in
`app/static/js/native.js`, and it is not cosmetic — each piece exists because
leaving it out causes a specific visible defect:

| Without it | With it |
|---|---|
| Top bar sits under the iPhone notch | Safe-area padding on the bar and tab bar |
| Back gesture quits the app mid-form | Closes sheets first, then needs a second press |
| Notification opens the dashboard | Deep link opens the actual task |
| Dark theme hides the status-bar clock | Status bar follows the theme |
| Losing signal looks like a crash | "Offline — showing what was last loaded" |
| Field typed into behind the keyboard | Scrolled into view, tab bar hidden |
| White flash on launch | Native splash held until first paint |

`native.js` is inert in a browser — verified: no `is-native` class, no banner,
no Capacitor. The web build is untouched.

---

## Build

```bash
npm install
npm run mobile:sync          # after any web or plugin change
```

**Android** (needs JDK 17 + Android SDK):

```bash
npm run android:debug        # android/app/build/outputs/apk/debug/app-debug.apk
npm run android:release      # android/app/build/outputs/bundle/release/app-release.aab
npm run mobile:android       # or open in Android Studio
```

**iOS** (needs macOS, Xcode, CocoaPods):

```bash
cd ios/App && pod install
npm run mobile:ios           # opens ios/App/App.xcworkspace
```

Then in Xcode: set the team, **Product → Archive → Validate → Distribute**.

---

## Signing — yours alone

Never commit a keystore or its password. Anyone holding both can publish an
update to your Play listing, and losing it means you can never update the
listing again, only publish a different app.

```bash
keytool -genkey -v -keystore profschedule.keystore \
  -alias profschedule -keyalg RSA -keysize 2048 -validity 10000
```

Then `android/keystore.properties` (gitignored):

```properties
storeFile=../../profschedule.keystore
storePassword=…
keyAlias=profschedule
keyPassword=…
```

`build.gradle` reads that file, or `ANDROID_KEYSTORE_PATH` and friends from the
environment in CI. It never contains the values.

---

## Environment

The backend URL lives in `capacitor.config.json` under `server.url`. It is
`https://profschedule-ai.onrender.com` — no localhost anywhere. For a local
backend, point it at your machine's LAN IP (not `localhost`, which on a phone
means the phone) and set `cleartext: true` temporarily.

Set on the server, for App Links and the TWA:

```
ANDROID_PACKAGE_NAME=com.profschedule.ai
ANDROID_SHA256_FINGERPRINTS=<upload-key>,<play-signing-key>
```

No secret of any kind is in the app. The AI key, database URL, VAPID private
key and JWT secret are all server-side, and the app only ever talks to the
API over HTTPS.

---

## Store submission — what is still needed from you

**Google Play**
- Developer account (US$25, one-off)
- Keystore, created as above
- Privacy policy URL — required, and this app holds names, departments and
  timetables, so it has to say so
- Data safety form: name, email, college, department, schedule content
- Phone screenshots

**Apple**
- Developer Program (US$99/year)
- A Mac with Xcode — there is no way around this
- Bundle ID `com.profschedule.ai` registered
- APNs key if push is added
- Privacy nutrition labels

---

## Known gaps, stated plainly

**Push is not yet native.** The web app uses VAPID Web Push, which does not
work inside a Capacitor WebView. `@capacitor/push-notifications` is installed
and configured but not wired: doing it properly means Firebase Cloud Messaging
on Android, APNs on iOS, a `google-services.json`, and a server change to send
through FCM as well as Web Push. That is a real piece of work and it has not
been done. Reminders currently arrive by Web Push in a browser, and in the app
only while it is open.

**Voice input** uses the browser speech engine through the WebView. The
`RECORD_AUDIO` permission and both iOS usage strings are declared, but this
has not been tested on a device — there is no microphone in this environment.

**Android builds now.** JDK 17 and the Android SDK (platform 34, build-tools
34) were installed to `E:/android-toolchain`, and both artifacts build:

| Artifact | State |
|---|---|
| `app-debug.apk` (4.5 MB) | Signed with Android's **debug** key. Installs on any phone. Not for Play. |
| `app-release.aab` (3.3 MB) | Play's format, **unsigned**. Needs your keystore. |

`android/local.properties` points gradle at the SDK. It is gitignored, so on
another machine set `sdk.dir` (forward slashes — a properties file eats single
backslashes, which is what made the first build fail).

**iOS cannot be built on this machine, and no simulator can run here.** The
iOS SDK and the Simulator ship only inside Xcode, which Apple releases for
macOS alone — unlike the Android SDK, which was simply a download away. There
is no Windows equivalent to install.

The way round it is a Mac you rent by the minute. `.github/workflows/ios.yml`
builds on a GitHub macOS runner, free for a public repository:

| Job | Needs an Apple account? | Produces |
|---|---|---|
| `simulator` | **No** | A `.app`, plus a screenshot of it actually running |
| `archive` | Yes (paid, US$99/yr) | A signed `.ipa` for App Store Connect |

The `simulator` job does more than compile: it boots an iPhone 15 simulator,
installs the app, launches it and screenshots the result. That is the check
that matters, because this project has already shipped an Android package
that built perfectly and opened to a blank screen.

Run it from the Actions tab, or push a change under `ios/`. Download the
`.app` artifact and drag it onto a Simulator on any Mac.

**It has been run, and it passed.** The build compiled, booted an iPhone 15,
installed the app, launched it and screenshotted the result: the landing page
rendered, loaded from the live Render deployment. Artifacts are in `dist/ios/`
(`App.app`, 4.0 MB, and `ios-launch.png`).

The screenshot immediately earned its keep. It showed the top bar overflowing
with "Get started free" clipped off the right edge -- a defect the compile step
could never have caught, and one nobody would have seen until an iPhone was in
their hands. Fixed and re-verified in a second run. This is exactly the failure
mode the Android side already hit, and the reason the job boots the app rather
than stopping at a green build.

For the `archive` job, add these repository secrets:

```
IOS_CERTIFICATE_P12        base64 of your .p12 distribution certificate
IOS_CERTIFICATE_PASSWORD   its password
IOS_PROVISIONING_PROFILE   base64 of your .mobileprovision
APPLE_TEAM_ID              10-character team ID
```

The certificate is imported into a throwaway keychain with a random password
and deleted with the runner. It never touches the repository.
