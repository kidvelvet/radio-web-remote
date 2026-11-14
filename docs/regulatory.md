# Regulatory note

This is not legal advice. It points to the rules; read them yourself.
Quotes are from the eCFR (Title 47 up to date as of 2026-11-12, read
2026-11-13). US regulations are public domain.

## Every operator

- **Hold a license with privileges for the band and mode you use.** "A
  station may only be operated in the manner and to the extent permitted by
  the privileges authorized for the class of operator license held by the
  control operator." ([47 CFR 97.105(b)](https://www.ecfr.gov/current/title-47/part-97/section-97.105))
  The server can enforce per-user band and power limits (set by an admin),
  but those are a convenience, not your license.
- **While you transmit through radio-web-remote, you are the control
  operator.** "When transmitting, each amateur station must have a control
  operator" ([97.7](https://www.ecfr.gov/current/title-47/part-97/section-97.7)),
  and the control operator "must ensure the immediate proper operation of the
  station, regardless of the type of control" ([97.105(a)](https://www.ecfr.gov/current/title-47/part-97/section-97.105)).
  Using this program from a browser is remote control: "the use of a control
  operator who indirectly manipulates the operating adjustments in the station
  through a control link" ([97.3(a)(39)](https://www.ecfr.gov/current/title-47/part-97/section-97.3)),
  and "when a station is being remotely controlled, the control operator must
  be at the control point" ([97.109(c)](https://www.ecfr.gov/current/title-47/part-97/section-97.109)),
  which is wherever your browser is.
- **The station licensee stays responsible too.** When the control operator
  is someone else, "both persons are equally responsible for proper operation
  of the station" ([97.103(a)](https://www.ecfr.gov/current/title-47/part-97/section-97.103)).
  The licensee designates control operators; the audit log records who held
  the transmitter and when.
- **The rules of the country where the radio is apply, not where you are.**
  For a radio in the United States, Part 97 applies to transmissions "from any
  place that is ... within 50 km of the Earth's surface and at a place where
  the amateur service is regulated by the FCC"
  ([97.5(a)(1)](https://www.ecfr.gov/current/title-47/part-97/section-97.5)),
  and reciprocal operating authority is tied to "an amateur station located at
  places where the amateur service is regulated by the FCC"
  ([97.107](https://www.ecfr.gov/current/title-47/part-97/section-97.107)).
  For a radio in another country, follow that country's regulator. The
  general principle is stated by the ARRL
  ([remote station operation](https://www.arrl.org/contest-remote-station-operation)),
  a secondary source. TODO(verify): a primary source (ITU Radio Regulations
  Articles 18 and 25, or the FCC) for stations outside the US.

## 47 CFR 97.213 (United States)

[97.213](https://www.ecfr.gov/current/title-47/chapter-I/subchapter-D/part-97/subpart-C/section-97.213)
is headed "Telecommand of an amateur station." It allows a station to be
under telecommand where, among other things:

> (a) There is a radio or wireline control link between the control point
> and the station sufficient for the control operator to perform his/her
> duties. If radio, the control link must use an auxiliary station. A control
> link using a fiber optic cable or another telecommunication service is
> considered wireline.
>
> (b) Provisions are incorporated to limit transmission by the station to a
> period of no more than 3 minutes in the event of malfunction in the control
> link.
>
> (c) The station is protected against making, willfully or negligently,
> unauthorized transmissions.
>
> (d) A photocopy of the station license and a label with the name, address,
> and telephone number of the station licensee and at least one designated
> control operator is posted in a conspicuous place at the station location.

How radio-web-remote relates to each (design in
[adr/0001-architecture.md](adr/0001-architecture.md)):

- **(b), the 3-minute limit.** The server unkeys when the browser stops
  sending heartbeats (2 s by default), when the connection closes, when the
  page is hidden, and when a transmission reaches the TX timeout, which is
  180 s at most and can only be set lower. Tested on all three radios
  ([hardware-test.md](hardware-test.md)). One case the server cannot cover:
  if the server process itself dies while keyed, the radio stays keyed
  (H7300-4 C, H705-4, H9700-7). Run it under a service manager that restarts
  it (the server sends TX off first thing), and turn on the radio's own
  Time-Out Timer ([radio-setup.md](radio-setup.md)).
- **(c), unauthorized transmissions.** Accounts with passwords, roles
  (listeners cannot transmit), a callsign required to transmit, per-user band
  and power limits, one transmitter at a time, and an audit log
  ([security.md](security.md)).
- **(a) and (d)** are the station's responsibility: the network link and the
  posted license and label.
