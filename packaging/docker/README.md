# Docker (optional, Linux only)

The install script is the supported way to run radio-web-remote
([docs/install-linux.md](../../docs/install-linux.md)). This image is for
people who already run Docker on the radio computer.

Build and run from the repository root:

```sh
docker compose -f packaging/docker/compose.yaml run --rm radio-web-remote radio-web-remote init
docker compose -f packaging/docker/compose.yaml up -d
docker compose -f packaging/docker/compose.yaml exec radio-web-remote radio-web-remote doctor
```

Run `init` first, with `run --rm`: until an admin account exists the server
exits at once (exit status 78), and Docker keeps restarting it, so `exec`
cannot reach it.

- **Serial port:** the container sees only the devices you pass in. Give the
  radio's port a stable name with a udev rule ([../udev/](../udev/)) and pass
  that, and set `match: port:` in `data/config.yaml` to the same path inside
  the container.
- **Audio:** passing `/dev/snd` gives the container the host's ALSA devices,
  including the radio's codec. The `audio` and `dialout` groups are added so
  the server can open them. If audio is silent or missing, run `doctor`
  inside the container.
- **Network:** `network_mode: host` lets the server see the host's LAN
  addresses, so its certificate covers them.
- **State** lives in `./data` next to the compose file, which is kept out of
  the image build. The container runs as an ordinary user (uid 1000); make
  `./data` writable for it (`sudo chown 1000 packaging/docker/data`).
- Not tested on hardware yet. TODO(verify) on a Raspberry Pi.
