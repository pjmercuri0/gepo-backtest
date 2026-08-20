# Mac mini production setup

This folder is the phase-1 setup kit for moving GEPO live ops from the 2020
MacBook Air to a dedicated Mac mini runner.

Target roles:

- Mac mini: IB Gateway + scheduled fetch/rank/freeze/track/health/upload jobs.
- Mya: public/display web server.
- MacBook Air: dev/backtest only after cutover.

## 0. Before the mini arrives

On the MacBook:

```bash
git status
git remote -v
git remote set-url origin https://github.com/pjmercuri0/gepo-backtest.git
```

Rotate the GitHub token that was previously embedded in the remote URL before
using this repo on another machine.

Decide which local live state to migrate:

```text
live/frozen/
live/intraday_picks/
live/ranked/
live/data/
```

These directories are mostly gitignored, so `git clone` alone is not enough.

## 1. Physical first boot

Start here when opening the box.

1. **Unbox.**
   - Mac mini.
   - Power cable.
   - Keep the box/serial paperwork until setup is complete.

2. **Connect temporary setup gear.**
   - Plug power into the Mac mini and wall.
   - Plug HDMI from Mac mini to TV/monitor.
   - Set the TV/monitor to that HDMI input.
   - Plug in a USB keyboard. If it is USB-A, use a USB-C hub.
   - Use any mouse. Bluetooth usually works, but a cheap/borrowed wired mouse is
     easier if pairing is annoying.

3. **Turn it on.**
   - Press the Mac mini power button.
   - Wait for the macOS setup screen.

4. **Complete macOS setup.**
   - Choose language/country.
   - Join home Wi-Fi.
   - Create the normal user account.
   - Sign into Apple ID if desired; GEPO does not require it.
   - Skip optional setup prompts that are not useful.

5. **Name the Mac.**
   Use something obvious:

   ```text
   gepo-mini
   ```

6. **Disable computer sleep.**
   In System Settings, set computer sleep to never. Display sleep is OK.

7. **Enable home remote access.**
   Turn on:

   ```text
   System Settings -> General -> Sharing -> Screen Sharing
   System Settings -> General -> Sharing -> Remote Login
   ```

8. **Test Apple Screen Sharing from the MacBook Air while at home.**

   ```text
   Finder -> Network -> gepo-mini -> Share Screen
   ```

   If Finder discovery is flaky, open the Screen Sharing app directly and
   connect to:

   ```text
   gepo-mini.local
   ```

9. **Set up away-from-home access.**
   On the Mac mini:

   - Install/open Chrome or Brave.
   - Open `https://remotedesktop.google.com/access`.
   - Set up Chrome Remote Desktop remote access.
   - Name the machine `gepo-mini`.
   - Set a PIN.
   - Test from the MacBook Air.

10. **Verify shell access.**
    From the MacBook Air, while at home:

    ```bash
    ssh username@gepo-mini.local
    ```

11. **Continue to repo setup.**
    Open Terminal on the mini and continue with section 2.

Summary:

- **At home:** use Apple Screen Sharing from the MacBook Air.
- **Away from home:** use Chrome Remote Desktop.
- **Terminal work:** use SSH through `Remote Login`.

After this, the mini can run headless.

## 2. Install basics

Install Xcode Command Line Tools:

```bash
xcode-select --install
```

Clone the repo:

```bash
git clone https://github.com/pjmercuri0/gepo-backtest.git
cd gepo-backtest
```

Run the bootstrap helper:

```bash
bash deploy/mac-mini/bootstrap.sh
```

The current cron wrappers use both `python3` and `/usr/bin/python3`, so the
bootstrap installs dependencies for both when they are different. The smoke test
checks both interpreters.

## 3. Configure production env

Copy the env template:

```bash
cp deploy/mac-mini/gepo.env.example ~/.gepo_env
chmod 600 ~/.gepo_env
```

Edit `~/.gepo_env` and set:

```bash
MYA_SSH_HOST="ubuntu@..."
MYA_REMOTE_BASE="/opt/vito/gepo-backtest/live"
IB_PORT=4001
```

Use `IB_PORT=4002` only for paper Gateway.

## 4. Copy live state

From the MacBook, replace `mini` with the mini's hostname:

```bash
rsync -az live/frozen live/intraday_picks live/ranked live/data mini:~/gepo-backtest/live/
```

Then on the mini:

```bash
cd ~/gepo-backtest
bash deploy/mac-mini/smoke_test.sh
```

## 5. IB Gateway smoke test

On the mini:

1. Start IB Gateway.
2. Log in.
3. Confirm API/socket clients are enabled.
4. Confirm live port `4001` or paper port `4002`.

Then run:

```bash
cd ~/gepo-backtest
bash deploy/mac-mini/smoke_test.sh --ibkr
```

Only run the full market pipeline when you are ready for an actual production
scan/upload:

```bash
bash deploy/mac-mini/smoke_test.sh --full
```

## 6. Install schedule

After one manual smoke test passes:

```bash
bash deploy/mac-mini/install_crontab.sh --dry-run
```

If the preview looks right:

```bash
bash deploy/mac-mini/install_crontab.sh
```

This script preserves any unrelated crontab lines, removes legacy
`gepo-backtest/live/cron_*.sh` lines, and installs the current production
schedule inside a managed block:

```text
1,31 9-16 * * 1-5  cron_parallel
1 16 * * 4,5       cron_expire
1,31 9-15 * * 4,5  cron_track_expiring
1 15 * * 4,5       cron_close_alert
1 17 * * 1-5       cron_daily_bars
1 17 * * 5         cron_calendar_refresh
*/5 9-17 * * 1-5   cron_health
31 17 * * 5        cron_pool_refresh
```

Once the mini completes one real market scan and Mya updates correctly, disable
the production crontab on the MacBook Air so both machines do not run live ops.

## 7. Tomorrow's acceptance checks

Do not call the migration complete until all of these are true:

- Apple Screen Sharing works from the MacBook Air while at home.
- Chrome Remote Desktop works from the MacBook Air for away-from-home access.
- `ssh mini-name` works.
- `~/.gepo_env` exists and has the Mya/IBKR settings.
- `bash deploy/mac-mini/smoke_test.sh` passes.
- IB Gateway is logged in on the mini.
- `bash deploy/mac-mini/smoke_test.sh --ibkr` gets a SPY tick or gives a clear
  Gateway/API failure.
- `bash live/upload_to_mya.sh` can sync to Mya.
- A scheduled cron firing runs on the mini.
- Production cron is disabled on the MacBook after the mini is confirmed.

## 8. Known risk

Using the same IBKR username on the mini and elsewhere can kick the Gateway/API
session. Phase 1 accepts this: after logging into IBKR elsewhere, remote into
the mini and relogin Gateway. If that becomes too annoying, create a second
IBKR username dedicated to the mini/API and deal with any market-data
entitlement cost separately.
