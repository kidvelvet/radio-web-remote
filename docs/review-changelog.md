# Code review changelog (pre-publication)

Started 2026-11-14 from v0.1.0 (commit 3e92f90), before making the
repository public.

## Method

Each round, independent reviewers read the whole product code in five
areas (CI-V and drivers; station, PTT and audio; web server and security;
browser client; CLI, doctor, config and packaging) and report bugs and
inefficiencies with a failure scenario. Each finding is checked against the
code before it is changed; findings that do not hold up are listed as
rejected with the reason. Every fix keeps the full test suite, the browser
tests and CI green.

**Stop rule:** the review ends after the first round whose changes are less
than **2%** of the product code. Change size = lines added plus lines
removed (`git diff --numstat`) in product code (`src/`, `packaging/`,
`scripts/`; not tests or docs), divided by the non-blank lines of product
code at the start of that round.

Product code at the start: 8,280 non-blank lines (Python 6,368; JavaScript
1,161; HTML and CSS 459; install and bootstrap scripts 292).

## Rounds

### Round 1 (2026-11-14): 1,128 lines changed of 8,280 = 13.6%: continue

Five reviewers, 70 findings. 58 fixed, 9 deferred, 3 rejected or folded into
another fix. Regression tests added for the fixes marked (T).

**PTT and transmit safety**

- A1 (T): an FA answering TX off (possibly a late FA for an earlier command)
  skipped unkey's read-back and resend. Unkey now treats any radio error as
  "not confirmed yet" and keeps reading back.
- A3: replies carry no command id. After a timeout the driver now waits 50 ms
  for a late FB/FA before the next command; a TX off sent past a stuck command
  no longer completes that command as acknowledged.
- A4 (T), C4: serial and closed-transport errors are now `RadioError`, so every
  caller's cleanup runs (it was skipped in `ptt_down`).
- B1 (T): a second operator's key could reach the radio before the previous
  holder's TX off, which then unkeyed the new holder. Keying now waits for any
  TX off in flight; a key overtaken from outside denies instead of leaving a
  holder who is not on the air.
- B2 (T): a keyed, band-limited user could retune out of band; the retune is
  now refused before it is sent.
- B3 (T): one NG reply in the poll or meter loop counted as a lost link, which
  dropped the transmitter and raised a false "TX off not confirmed". NG now
  skips that item (after 3 in a row it is no longer polled); the reconnect
  backoff only resets after the link stayed up 10 s.
- B9: an unkey overtaken by a new key was logged as "TX off confirmed"; it is
  now logged as superseded.
- B10: a PTT release could wait behind a slow control command on the same
  connection. Controls now run in a per-connection worker; PTT, heartbeats and
  visibility are handled as they arrive.
- B11 (T): keying is refused after the controller is closed.
- C3: at connection teardown a scope command ran before the unkey, and a
  cancelled teardown could skip the unkey entirely. Teardown now runs as a
  task started before any await: unkey first, then the scope update.
- D1: after a TX timeout the page never sent the release the server waits for,
  so PTT stayed dead until reload. Fixed for hold and toggle modes (probe).
- D2: "TX OFF NOT CONFIRMED" was overwritten by "Radio not connected".
- B7: users without band limits no longer depend on the TX frequency read.
- B8: front-panel retunes while keyed are coalesced into one recheck at a
  time; background tasks are cancelled at close.

**Security**

- C1: password hashing shared the default thread pool with serial writes
  (TX off included), so a login burst could delay an unkey by seconds. Serial
  writes now have their own thread per port; logins hash on a separate pool
  of two; each attempt takes a rate-limit slot before hashing.
- C2 (T): the lockout counter raced (68 password checks instead of 5 in a
  burst); it is now an atomic increment.
- C5: requests that need no login could rotate the transmit history out of the
  audit log; rejection events are now logged at most once a minute per
  address, with a count.
- C9: rate-limit keys group IPv6 by /64; stale keys are purged properly.
- C10 (T): a locked account answered differently from an unknown name; unknown
  names now lock the same way.
- C7 (T): floats and booleans passed integer checks (`edge: 2.0` dropped the
  connection, `filter: true` was broadcast).
- C8: a failed session re-check ended revalidation for good, and a dead
  per-connection task left a half-working client; both now recover or close.
- C6: control messages are sent before queued audio, so state and errors do
  not wait behind audio on a slow link.
- C11: the HTTP redirect broke for IPv6 hosts and dropped the query string.
- C14: the server key and certificate are written atomically as a pair.
- C15: expired sessions are purged.
- E4: the Docker example could bake users.db and config into an image layer;
  state is excluded from the build and the container runs as a normal user.
- E10: serial ports are opened exclusively on Linux and macOS, so `probe` or
  `doctor` cannot share the radio's port with a running server.

**Radio, scope and audio**

- A2 (T): 07 B0 (swap) was retried on timeout; a lost FB made it swap back.
- A5: an out-of-range scope sweep completes at its header.
- A6: an unconfirmed unkey after finding the address no longer fails connect.
- A7: unkey confirmation reads once per poll, keeping to its 0.5 s budget.
- A8: scope clipping uses a lookup table (25 us to 0.2 us per line).
- B4 (T): a half-started audio backend leaked streams on every retry.
- B5: an audio device that stops delivering (unplugged, radio off) is noticed
  and reopened.
- B6 (T): `auto` audio matching now works with Linux and Windows device names
  and picks the default host API among Windows duplicates.
- D3, D4: a double tap on Resume audio built two audio pipelines; a failed
  start left the microphone open (both reproduced, both fixed).
- D5: Space did not key PTT with a checkbox or slider focused.
- D6: an expired session looped "Reconnecting"; it now goes to the login page.
- D7: "1296.1" in direct entry was read as kHz on an IC-9700.
- D8, D9: sliders stopped following the radio after a tap; a coasting flick
  overwrote a band jump.
- D10, D11, D12, D13: status text is written only when it changes and the TX
  level moved out of the announced live region; the scope reads its scale
  and colours once instead of per pixel or per line; the mic meter updates at
  10 Hz; each receive frame is no longer copied.

**Setup and tooling**

- E1 (T): doctor called every bind failure "port in use" and skipped the radio
  checks.
- E2: a YAML syntax error, or any config error in init/cert/serve/user,
  printed a traceback.
- E3 (T): config values that loaded but broke the server are now rejected
  (redirect port equal to the HTTPS port, invalid hostnames, civ_address equal
  to the controller address, boolean or negative audio indexes,
  `ptt_method: dtr|rts`, which was never implemented and is now documented as
  such). Blank cert paths mean unset. An empty `match:` now picks the one
  connected radio of the configured model, as the docs promised (T).
- E5, E15: install.ps1 rewrote the user PATH with expanded variables, leaked
  `$ErrorActionPreference` into the session under `irm | iex`, and split a
  Python path with spaces.
- E6, E7, E8: install.sh did not restart a running service on upgrade, opened
  a second macOS server loop, deleted the program under a running server, kept
  a venv without pip, and never found PortAudio on Debian (ldconfig not on
  PATH).
- E9 (T): doctor's config suggestion missed an IC-9700 whose port B sorts
  first, and suggested port B of an already configured IC-705.
- E11: the last enabled admin can no longer be disabled.
- E12: services start in the data folder, so a stray config.yaml elsewhere is
  not picked up; systemd stops a crash loop after 10 tries in 5 minutes.
- E13, E14, E17: `init` without a terminal, `--password-stdin` without
  `--username`, `--config` after `user`/`cert`, and the restart reminder after
  `cert reset`.
- E18: CI runs both installers the way members do (piped `sh`, `Invoke-Expression`)
  and checks their version against pyproject.toml.

**Deferred** (recorded, not changed in this round): A9 (two callbacks per
serial read; negligible), A10 (transport priority queue used only by tests),
A11 (DTR/RTS pulse at open on Linux; needs Linux hardware), B12 removed rather
than fixed, C12 (scope toggle rate and connections per session), C13 (rotating
IPv6 addresses in the certificate), E15 Windows task restart behaviour and E16
Windows `usb_serial` matching (both need a Windows test).

**Rejected**: C-inefficiencies (measured negligible by the reviewer, 8 to 20
us), B-per-frame audio resampling (under 0.4% of a frame), A8 parser cost
(0.12 ms per second of scope traffic; only the clipping was changed).

### Round 2 (2026-11-14): 719 lines changed of 8,813 = 8.2%: continue

Five fresh reviewers, who also checked the round-1 changes for regressions.
41 findings: 40 fixed, 1 deferred. (T) = regression test added; (P) =
confirmed with the reviewer's browser probe.

**PTT and transmit safety**

- A2-1 (T): an unkey's resend could undo a key that landed while its
  read-back was in flight (reproduced). The resend now re-checks for a newer
  key while holding the bus.
- A2-2: the unkey confirmation now has one deadline from the first TX off
  (0.5 s); each step's wait counts against it (a silent radio took 1.7 s).
- A2-4: "superseded" now means superseded by a key only; a newer unkey keeps
  confirming instead of clearing the alarm.
- B2-1 (T): waiting for in-flight unkeys with `asyncio.gather` meant that
  cancelling the waiting key also cancelled another operator's TX off; it now
  uses `asyncio.wait`.
- B2-2 (T): a key queued behind an unkey when the station closed still keyed
  the radio after the watchdog had stopped; it is now refused.
- B2-3: a PTT press (urgent) could overtake a limited user's queued retune
  (normal priority) and pass the band check on the old frequency; the retune
  and its check now hold the key lock.
- B2-5: the watchdog's TX-off retry now runs as a tracked task that a new key
  waits for.
- B2-7: a confirmed key clears a stale "TX off not confirmed" alarm instead of
  showing it through the next operator's whole transmission.
- C2-4: a PTT press could overtake a power or band change sent just before
  it; a key now waits (2 s at most) for earlier controls, a release never does.
- D2-1 (P): the late "released" state from the user's own release, arriving
  after a quick re-press, was taken as a server stop: the radio stayed keyed
  with the PTT button disabled and no microphone audio (all three browsers).
- D2-2 (P): in toggle mode, a tap meant as "stop" right after a server timeout
  re-keyed for another full period; that tap now only releases.
- D2-5: in hold mode, a second finger tapping anywhere released PTT; only the
  pressing pointer releases now.

**Robustness**

- A2-3 (T), B2-6: a well-framed reply in an unexpected form raised a
  `ValueError` that callers did not handle (the link was declared lost, or a
  TX limit recheck was skipped); it is now `RadioBadReply`, skipped per item.
- B2-4: once host audio had failed, every host start reset PortAudio and
  killed the radio's streams; the device-list reload now runs in the router
  with every stream closed.
- B2-8: items skipped after repeated NG are polled again after a reconnect.
- C2-3 (T): the new control queue dropped the newest message when full (the
  dial snapped back); controls now coalesce, newest value kept in place.
- C2-8: routine disconnects no longer log as errors.
- C2-9: the server key and certificate are flushed to disk before being
  swapped in, and a mismatched or unreadable pair is reissued at start (a
  broken CA is reported, not replaced).
- A2-5: radio identity strips the IC-9700's " A"/" B" suffix exactly; the
  "N radios connected" message counts radios, not ports.
- A2-6: serial ports also get the kernel's TIOCEXCL lock (flock alone is
  advisory and only stopped our own tools).

**Security**

- C2-1 (T): on a dual-stack bind every IPv4 client shared one rate-limit key,
  and a private IPv6 /64 grouped the whole LAN: one person could lock everyone
  out. IPv4-mapped addresses are keyed by their IPv4 address, and only global
  IPv6 is grouped by /64.
- C2-2: the lockout counter used `UPDATE ... RETURNING`, which SQLite before
  3.35 rejects (lockout silently off); now an UPDATE then SELECT in one
  transaction.
- C2-5 (b): unknown-name lockout state now expires by age, so a flood of
  names cannot reset it.
- C2-6: locked-out attempts are logged once a minute per name, and failed
  logins at most 60 a minute in all with a count of the rest.
- C2-7: the rate limiter sweeps its keys at most every 5 s (it rescanned
  10,000 keys on every login during a flood).

**Browser**

- D2-3 (P): while disconnected the status text was written twice per message.
- D2-4: the 10 Hz mic meter shows the loudest frame since the last draw.
- D2-6: a bookmark to a radio that no longer exists shows a message instead of
  reconnecting forever.

**Setup and tooling**

- E2-1: install.sh now finds a server started through the command link, and
  stops the macOS restart loop before the server (it restarted it after
  uninstall).
- E2-2: the systemd start limit stopped restarts for good after a first run
  before `init`, and after any crash loop; instead `serve` exits 78 when setup
  is incomplete and the unit does not restart on 78 only.
- E2-3 (T): `init --password-stdin` without `--callsign` read the password as
  the callsign.
- E2-4: doctor reported permission and port-in-use problems as "no CI-V
  answer" (pyserial raises its own exception type); it now says which.
- E2-5: config errors are their own type (`ConfigError`, exit 78); missing
  files and an unwritable data folder print one line; other errors keep their
  traceback.
- E2-6 (T): `--data-dir X` now uses `X/config.yaml`.
- E2-7 (T): config errors name the field (`server.https_port: ...`).
- E2-8: doctor checks your own certificate files (readable, matching, not
  expired).
- E2-9: install.ps1 rebuilds a venv without working pip, stops a running task
  before upgrading and restarts it, and uses the data folder as the task's
  working folder.
- E2-10: the systemd unit sets the data folder explicitly and quotes the
  program path; the unused variable is gone.
- E2-11: the no-terminal message no longer gives wrong advice.
- E2-12: CI also runs install.ps1 under Windows PowerShell 5.1 and runs
  install.sh twice (the upgrade path).

**Deferred**: C2-5 (a), a 0.5 ms timing difference between a wrong password
on a real account (a database write) and on an unknown name, measured on an
SSD. Making them equal means a dummy write per unknown name; left for later.
