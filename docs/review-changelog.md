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

### Round 3 (2026-11-14): 359 lines changed of 9,161 = 3.9%: continue

Five fresh reviewers, again asked to look hard at the previous round's
changes. 35 findings, all fixed; two have a part deferred (below). Six were
regressions or gaps left by round 2 (A3-1, A3-2, B3-1, C3-1, C3-4, D3-1).
(T) = regression test added, failing before the fix.

**PTT and transmit safety**

- C3-1 (T): a PTT press waiting for earlier controls (round 2, C2-4) blocked
  reading the socket, so a release sent meanwhile was read only after the key:
  the radio transmitted with nobody pressing. The waiting press is now a task;
  a release, a hidden page or a closed socket cancels it while it waits, or
  lets it finish and then unkeys.
- B3-1 (T): a limited user's retune that passed the "held by another" check
  before another operator's press could still go out under that operator's
  carrier once the press keyed (reproduced). The retune guard now holds the
  key lock for every user and refuses while someone else holds TX.
- A3-1 (T): a garbled TX-state answer escaped unkey's read-back (round 2 had
  bypassed the helper that caught it): no further read-back, no resend, and a
  failed connect. It now counts as one more miss.
- A3-2 (T): after a poll whose reply was lost held the bus until its timeout,
  unkey's read-back went out with no time left and missed the radio's RX
  answer, giving a false "TX off NOT confirmed". Each unkey wait now gets at
  least the 50 ms late-reply window.
- B3-7: the watchdog no longer starts a TX-off retry while a press is keying;
  a failure of that retry left the alarm up during the new transmission.

**Station and audio**

- B3-2 (T): reloading PortAudio's device list (to see a radio switched on
  later) closes every stream in the process, so with two radios one retrying
  station cut the other's audio every 10 s. The reload is skipped while any
  other stream is open.
- B3-5 (T): a missing host headset stopped radio audio for remote operators
  too. The radio codec now decides whether audio runs; the host devices are
  retried on their own.
- B3-6: a failed reload no longer leaves PortAudio uninitialised for good.
- B3-3: a late FA answering a poll counted as a lost link (TX dropped,
  reconnect); it now skips that poll.
- B3-4: one lost reply among the ~20 reads at connect failed the whole
  connect; such a read is now retried by the round robin, and only several in
  a row count as a lost link. A refused scope re-enable is logged.
- A3-3 (T): a short span answer is rejected instead of shown as a span the
  radio never had.

**Web server and security**

- C3-2 (T): coalescing let a frequency meant for the new band overwrite one
  for the old band and run before the band change (IC-9700). Band changes,
  swaps and Dualwatch are now barriers.
- C3-7 (T): made-up level or setting names each took a queue slot without
  limit; every distinct entry now counts against the cap (raised to 32 so all
  real controls fit).
- C3-3 (T): an empty or truncated server certificate crashed the start
  instead of being reissued.
- C3-4 and E3-3 (T): a broken local CA printed a traceback and exited 1, so
  systemd restarted it forever; it is now a config error (one line, exit 78).
- C3-5: address-throttled audit lines (bad-origin WebSocket attempts, rate
  limits, lockouts) also have the per-minute global cap, and the throttle
  table forgets its oldest keys instead of all of them at once.
- C3-9: the "N more suppressed" audit line is written within a minute of the
  flood ending, not at the next failed login.
- C3-6 (T): a flood of unknown names could evict, and so unlock, a locked
  unknown name; unlocked names go first.
- C3-8: only a send after the socket closed counts as a routine disconnect;
  other RuntimeErrors are logged as errors.

**Browser**

- D3-1 (T): in toggle mode, the first tap after a refused press only
  released (round 2 meant that only after a server stop).
- D3-2: when the link drops, the TX bar no longer keeps showing ON AIR.
- D3-3 (T): a mouse click on another control dropped a Space press.
- D3-4: the "no such radio" message is shown once, not on every reconnect.
- D3-5 (T): band-limit and radio refusals no longer show the internal code.

**Setup and tooling**

- E3-1: install.ps1's venv check stopped the script under Windows PowerShell
  5.1 (redirected stderr becomes an error there); CI now runs that repair
  path under 5.1.
- E3-2: a failed upgrade left the Windows task stopped; it is restarted in
  all cases, and the venv is repaired only after the server is stopped.
- E3-4 (T): your own certificate that is missing or does not match, and a
  damaged user database, are config errors naming the file; `serve` prints a
  traceback for other file errors, so the service log shows where.
- E3-5 (T): doctor reports a damaged user database and a passphrase-protected
  key as findings instead of stopping.
- E3-6: the Docker instructions run `init` before `up` (before an admin
  exists the container restarts, so `exec` cannot reach it).
- E3-7: install.sh escapes the install paths in its process patterns (a
  path with brackets, parentheses or `+` hid a running server from
  uninstall), and `--service` refuses paths with `%` or `\`, which systemd
  would read.
- E3-8: install.sh waits up to 5 s for the server to finish shutting down
  (it sends TX off) before removing files.
- E3-9 (T): `--data-dir X` now comes before a `./config.yaml`;
  `config check` takes `--data-dir`.
- E3-10 (T): the bare-number address error names the file once.
- E3-11: stale comments fixed; the user unit's `After=` on system targets
  (which a user unit cannot see) is gone.

**Deferred**: B3-2 (b), one host audio backend per radio opens the same host
devices more than once; sharing one is a larger change, and only matters with
two radios and host audio. E3-5 (b), doctor resolves a relative `cert_file`
against its own working folder, while the service uses the data folder.

### Round 4 (2026-11-14): 212 lines changed of 9,324 = 2.3%: continue

Five fresh reviewers. 23 findings, all fixed; one has a part left as a
documented limit (B4-3). Five came from round-3 changes (B4-1, B4-2, D4-1,
E4-1, E4-3). (T) = regression test added, failing before the fix.

**PTT and transmit safety**

- D4-1 (T): the server did not say which press a refusal answered, so a late
  refusal of an earlier press cleared the page's PTT after a newer press had
  keyed; the radio stayed keyed with the page showing receive, and in toggle
  mode the next tap keyed again (reproduced). Each press now carries a number
  that the refusal echoes, and the page ignores refusals of older presses.
- B4-1 (T): round 3 made the TX-off retry wait while the key lock was held,
  and every retune now holds it, so a VFO drag could starve the retry of an
  unconfirmed TX off. The retry no longer looks at the lock; a TX off that
  fails after a newer key was confirmed no longer raises the alarm.
- B4-2 (T): a press waiting behind another user's retune (or a TX off) still
  waited inline, so its release was read only after the key (the C3-1 case
  by another route). Such a press now waits as a task too, cancellable until
  the controller commits it.
- A4-1 (T): one lost read-back answer used up the whole 0.5 s window. Each
  read-back now waits one resend interval, so a lost answer is retried.
- A4-2 (T): after a stuck command, unkey now fails that command and takes
  the bus next (ahead of its retry), instead of waiting out its full
  timeout; the read-back then has the whole window.

**Station and audio**

- B4-4: a scope on/off the radio refused was never retried until viewers
  changed; it is now retried every 5 s while it differs from what viewers
  want.
- B4-3: a device connected after start is not found while other audio runs
  (PortAudio reloads its list only with every stream closed). The status now
  says a restart finds it; a coordinated reload is left for later.
- B4-5: an error without text shows its type.
- A4-3: the simulator answers NG to a wrong-length span instead of stopping.

**Web server and security**

- C4-1: a user database with damaged data pages now also stops the start as
  a config error (the check happened after the guarded open).
- C4-2 (T): values of the wrong type (a list as the message type, a huge
  number as the reference level) closed the connection with a traceback; they
  are now refused as bad requests.
- C4-3 (T): a server certificate with no names (not ours), or unreadable, is
  reissued instead of stopping the start.
- C4-4: a throttled address's own count is kept when the global cap
  suppresses it, and the last minute's counts are written at shutdown.
- Ending a session (logout, disable) now also settles a waiting press first.

**Setup and tooling**

- E4-1 (T): round 3's config order made doctor read the data folder's
  config.yaml while `serve` read ./config.yaml; doctor now looks up config
  exactly as `serve` does.
- E4-2 (T): doctor reports a broken or half-missing local CA (which stops
  `serve`), instead of passing or crashing; `serve` no longer silently makes
  a new CA when only one of the two files is there.
- E4-3: with `-Service`, a failed upgrade left the task stopped.
- E4-4: the Docker instructions create `data/` owned by the container user
  first, and say the radio must be connected.
- E4-5: database errors name users.db for every command.
- E4-6: bootstrap.ps1 stops on a failed step instead of printing "Ready".

**Tests**: the browser-test server's log went to a pipe nobody read; at
64 KB (reached by the end of a CI run after round 3 added tests) the server
blocked and the last WebKit test timed out in CI. It now logs to a file.

### Round 5 (2026-11-14): 201 lines changed of 9,443 = 2.1%: continue

Five fresh reviewers. 13 findings, all fixed. Four came from round-4 changes
(B5-1, C5-1, C5-2, A5-1). (T) = regression test added, failing before the
fix; (P) = confirmed with the reviewer's browser probe.

**PTT and transmit safety**

- D5-1 (P): the page took another operator's stop (time limit, hidden page)
  as its own while its press was in flight; the server then keyed that press
  with the page showing it released and sending no audio. The page now acts
  only on the stop of a transmission it showed as its own.
- C5-1 (T): a press that did not have to wait for anything still ran inline,
  so a release during its transmit-frequency read (IC-705, IC-9700) was read
  after the key, and a logout could not cancel it. Every press now waits as
  a cancellable task.
- C5-2: round 4's catch of TypeError and OverflowError for bad client values
  also hid server bugs, including one after a key (the radio stayed keyed
  with no error). Bad values are now checked where they arrive and only
  ValueError is reported as a bad request; anything else closes the
  connection, which unkeys.
- B5-1 (T): with round 4's change, a TX-off retry could start during a
  press's transmit-frequency read and land its TX off after the key (the
  radio unkeyed under a holder shown as on air). No retry starts during that
  read.
- A5-1 (T): TX off now keeps its place in the bus queue while it waits for a
  stuck command; an urgent read queued meanwhile went out first.
- A5-2 (T): a command failed so that TX off could go first now retries only
  after the TX off is confirmed, not in the read-back's way.

**Station**

- B5-2 (T): after a reconnect or restart with no viewers, the radio could
  keep streaming scope data nobody watched (about 2.7 KB/s); the scope state
  is unknown after connect and is set either way.
- A5-3: the simulator keeps its span when it refuses a new one.

**Web server and security**

- C5-3: per-address audit counts for a key that went quiet were never
  written; they are flushed every minute and at shutdown.
- C5-4 (T): the start check now runs SQLite's quick check over the whole user
  database (a damaged sessions table passed and made every login fail).

**Setup and tooling**

- E5-1 (T): with both CA files gone but the server certificate they issued
  still there, `serve` made a new CA without a word and doctor said ok; both
  now stop and say so. `serve` and doctor share one check.
- E5-2 (T): the CA messages name the missing file and offer restoring from a
  backup before `cert reset`; an unreadable file says to check its owner.
- E5-3: the e2e fixture shows the server log when the server does not start,
  and stops it.

### Round 6 (2026-11-14): 161 lines changed of 9,495 = 1.7%: stop

Five fresh reviewers. 16 findings: 15 fixed, 1 documented (D6-2). Under 2%,
so the review ends here. (T) = regression test added, failing before the
fix; (P) = confirmed with the reviewer's browser probe.

**PTT and transmit safety**

- D6-1 (P): round 5's "our own transmission" test trusted any transmission
  shown under our callsign, so another tab of the same account, or our own
  earlier transmission being stopped, could still drop a press the server
  then keyed. A transmission already on air when a press is sent is no
  longer counted as that press's.
- A6-1 (T): a command that took the bus between unkey's read-backs and lost
  its reply pushed the confirmation past its window (radio on air about
  0.77 s, then a false alarm). Every step of the confirmation now overtakes a
  stuck command.
- A6-2 (T): a lost TX off was resent at about 0.36 s instead of 0.15 s; the
  resend interval now counts from the write.
- B6-1: a TX-off retry could still start in the loop pass between a press's
  wait for an earlier TX off and its key. No retry starts from the moment a
  press takes the key lock until it has keyed.
- C6-1 (T): since every press became a task (round 5), a control read in the
  same burst after a press was waited for, and could run first (a band
  change before the key). Whether a press waits is now decided when it
  arrives.

**Web server and security**

- C6-2: a flushed per-address count that the global cap suppressed added 1,
  not its count.
- C6-3, E6-1 (T): doctor now runs the same database check as `serve`, puts a
  local-CA failure under "What to fix" (it printed "Nothing to fix." under a
  FAIL), and checks the CA before saying the server certificate "will be
  re-issued".
- C6-4, E6-2 (T): a CA file this account cannot read is named and gets only
  the ownership advice; `cert reset` there would replace a good CA. Every
  certificate message names the file at fault.
- A6-3: the driver interface's unkey description matches what it does.

**Setup and tooling**

- E6-3: .dockerignore now keeps keys and certificates out of the image at any
  depth (`**/*.key`, `**/*.pem`, `**/*.p12`, `**/*.pfx`, `**/certs`), and the
  build cache no longer depends on `dist/` and `.hypothesis/`.
- E6-4: the e2e setup shows why a setup command failed, and CI uploads the
  test server's log when the browser tests fail.
- D6-3: an unused parameter removed.

**Documented (D6-2)**: if the link stalls right after a press for longer than
the heartbeat timeout, the page can receive only the final "stopped" state
and keep its button shown as pressed with no reason. Nothing is transmitted
(the server stopped it); the next tap releases. Fixing it needs the server to
say whose transmission stopped.

## Summary

| Round | Base lines | Changed | Share | Findings fixed |
|---|---|---|---|---|
| 1 | 8,280 | 1,128 | 13.6% | 58 |
| 2 | 8,813 | 719 | 8.2% | 40 |
| 3 | 9,161 | 359 | 3.9% | 35 |
| 4 | 9,324 | 212 | 2.3% | 23 |
| 5 | 9,443 | 201 | 2.1% | 13 |
| 6 | 9,495 | 161 | 1.7% | 15 |

184 findings fixed in six rounds; the deferred items are listed in each
round. Every fix was checked against the code before it was made, and the
full unit suite (366 tests) and browser suite (Chromium, Firefox, WebKit)
pass.
