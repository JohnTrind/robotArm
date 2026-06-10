# Phase 0: kick a soft-reset once per power-on so the LCD renders. We
# observed the ST7789 silicon needs a beat to come up after PWRON before
# its registers respond -- without this pause the soft-reset fires too
# early and the panel still ships up blank. The delay is configurable via
# config.boot.kick_delay_ms (default 1500). Done before any other imports
# so the bootstrap stays cheap on the failed-render pass.
import machine
import time

def _boot_kick_delay_ms():
    try:
        import json as _j
        with open("/config.json") as _f:
            return int(_j.load(_f).get("boot", {}).get("kick_delay_ms", 1500))
    except Exception:
        return 1500

if machine.reset_cause() in (machine.PWRON_RESET, machine.HARD_RESET):
    _delay = _boot_kick_delay_ms()
    if _delay > 0:
        time.sleep_ms(_delay)
    print("[main] cold boot: soft-reset to wake the lcd")
    machine.soft_reset()

print("[main] start")
import asyncio
from core import os as kernel
from core import display, led, settings, wifi_manager, sd, touch
print("[main] imports ok")

cfg = settings.get()
print("[main] config:", cfg.get("hostname"), "wifi networks:", len(cfg.get("wifi", [])))

display.init(rotation=cfg.get("rotation", 0), brightness=cfg.get("brightness", 255))
print("[main] display init ok")
led.init()
print("[main] led init ok")
kernel.set_screensaver_ms(cfg.get("screensaver_ms", 0))

# Frog is the home screen on lite. Register first so kernel.run() picks it
# as the default current app.
frog = None
try:
    from apps.frog import Frog
    frog = Frog()
    kernel.register_app(frog)
    print("[main] frog registered")
except Exception as e:
    print("[main] frog error:", e)


def status(line):
    """Print a boot/status message AND show it in the frog's dialog bubble."""
    print("[main]", line)
    if frog is not None:
        try:
            frog.push(line)
        except Exception:
            pass


# System apps stay registered so the web /admin and /files settings keep
# working; they just don't appear on the LCD anymore (no launcher menu).
try:
    from apps.about import About
    kernel.register_app(About())
except Exception as e:
    print("[main] about error:", e)

try:
    from apps.settings_app import Settings
    kernel.register_app(Settings())
except Exception as e:
    print("[main] settings error:", e)

try:
    from apps.wallpaper import Wallpaper
    kernel.register_app(Wallpaper())
except Exception as e:
    print("[main] wallpaper error:", e)

print("[main] apps:", [a.name for a in kernel.list_apps()])


_boot_cfg = cfg.get("boot", {})


async def _sync_apps_task():
    """Background sync: hit the espApps repo for a newer version.json and
    download the listed apps if it differs. Runs after the web server is
    up so it doesn't delay first responses."""
    await asyncio.sleep_ms(1500)
    repo = _boot_cfg.get("apps_repo")
    if not repo:
        return
    branch = _boot_cfg.get("apps_branch", "main")
    blocked = _boot_cfg.get("apps_blocked") or []
    status("syncing apps...")
    try:
        from core import updater
        n, msg = updater.sync_apps(repo, branch, blocked=blocked)
    except Exception as e:
        print("[main] sync error:", e)
        status("sync error")
        return
    if n > 0:
        try:
            from core import apps_loader
            apps_loader.discover(blocked=blocked)
        except Exception as e:
            print("[main] reload after sync failed:", e)
        status("synced {} apps".format(n))
    else:
        status("sync: " + msg)


async def _startup():
    await asyncio.sleep_ms(500)
    status("mounting sd...")
    sd.mount()
    try:
        from core import apps_loader
        blocked = _boot_cfg.get("apps_blocked") or []
        n = len(apps_loader.discover(blocked=blocked))
        status("sd apps: " + str(n))
    except Exception as e:
        status("apps load err")
        print("[main] apps_loader error:", e)
    status("wifi...")
    if await wifi_manager.connect_or_ap(cfg.get("wifi", []), hostname=cfg.get("hostname")):
        ip = wifi_manager.ip() or "?"
        status("ip " + ip)
        from core import web_server
        asyncio.create_task(web_server.serve())
        status("web on :80")
        if _boot_cfg.get("apps_sync", False) and _boot_cfg.get("apps_repo"):
            asyncio.create_task(_sync_apps_task())
    else:
        status("wifi failed")
    status("ready")


async def _main():
    await asyncio.gather(kernel.run(), _startup(), touch.run())


print("[main] entering asyncio.run")
try:
    asyncio.run(_main())
except KeyboardInterrupt:
    print("[main] interrupted")
except Exception as e:
    print("[main] asyncio error:", e)
    import sys
    sys.print_exception(e)
