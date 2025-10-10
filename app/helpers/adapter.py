import functools
import logging
import platform
import subprocess


# ################################################################################
# typing annotations to avoid circular imports


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config


# ################################################################################
# adapter


class Adapter:
    # https://answers.microsoft.com/en-us/windows/forum/all/solved-unable-to-stop-internet-connection-sharing/b01e1ebc-4f9d-4bf6-8d15-37a782fa03ff
    # use this 'netstat -ab -p udp' to find listener

    @staticmethod
    def required_platform(allowed: str = "windows"):
        def decorator(func):
            @functools.wraps(func)
            def wrapper(self, *args, **kwargs):
                if not self.is_platform_supported(allowed):
                    return None

                return func(self, *args, **kwargs)

            return wrapper

        return decorator

    def __init__(self, config: "Config"):
        self.interface = config.interface
        self.ssid = config.ssid

    def is_platform_supported(self, allowed: str = "windows"):
        os = platform.system().lower()

        if os != allowed:
            logging.warning(f"unsupported platform, {os}. skipping.")
            return False

        else:
            return True

    def run_command(
        self, command: list[str], success_message: str | None = None
    ) -> str | None:
        try:
            result = subprocess.run(
                command,  # the shell command to execute (string or list)
                capture_output=True,  # collect stdout/stderr instead of printing to terminal
                text=True,  # decode output as str (not bytes) using default encoding (UTF-8)
                check=True,  # raise CalledProcessError if command exits with non-zero status
                shell=False,  # run command through the shell (e.g. bash/sh)
                timeout=60,  # kill process if it runs longer than 60 seconds
            )
            if success_message:
                logging.info(success_message)

            return result.stdout.strip()

        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as err:
            logging.error(f"'{command}' failed. error: {err}")

        except Exception as err:
            logging.exception(f"unexpected {err=}, {type(err)=}, {command}")

        return None

    # netsh wlan show profiles interface="wi-fi"
    # netsh wlan connect ssid=default name=default
    @required_platform("windows")
    def connect(self):
        self.run_command(
            ["netsh", "wlan", "connect", f"ssid={self.ssid}", f"name={self.ssid}"],
            success_message=f"{self.interface.lower()} connected to {self.ssid}!",
        )

    # netsh interface ipv4 show config wi-fi
    @required_platform("windows")
    def get_dns(self):
        output = self.run_command(
            ["netsh", "interface", "ipv4", "show", "config", self.interface],
            success_message=f"retrieved dns configuration for {self.interface.lower()}.",
        )

        if output:
            logging.info(f"current dns configuration:\n\n{output}\n")

    # netsh interface ipv4 set dns wi-fi dhcp
    @required_platform("windows")
    def reset_dns(self):
        self.run_command(
            ["netsh", "interface", "ipv4", "set", "dns", self.interface, "dhcp"],
            success_message=f"reset {self.interface} dns settings to automatic.",
        )

    # netsh interface ipv4 set dns wi-fi static 127.0.0.1 validate=no
    @required_platform("windows")
    def set_dns(self, primary_dns: str = "127.0.0.1"):
        self.run_command(
            [
                "netsh",
                "interface",
                "ipv4",
                "set",
                "dns",
                self.interface,
                "static",
                primary_dns,
                "validate=no",
            ],
            success_message=f"set {self.interface} primary dns to {primary_dns}.",
        )
