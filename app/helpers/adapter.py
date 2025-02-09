import logging
import platform
import subprocess

from typing import Optional


class Adapter:
    # https://answers.microsoft.com/en-us/windows/forum/all/solved-unable-to-stop-internet-connection-sharing/b01e1ebc-4f9d-4bf6-8d15-37a782fa03ff
    # use this 'netstat -ab -p udp' to find listener

    def __init__(self, config):
        self.interface = config.interface
        self.ssid = config.ssid

    def run_command(self, command: list, success_message: Optional[str] = None):
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=True,
            )
            if success_message:
                logging.info(success_message)
            return result.stdout

        except subprocess.CalledProcessError as err:
            logging.error(f"command failed: {command}. error: {err}")

        except Exception as err:
            logging.exception(f"unexpected {err=}, {type(err)=}, {command}")

        return None

    def supported_platform(self):
        os = platform.system().lower()

        if os != "windows":
            logging.warning(f"unsupported platform, {os}. skipping.")
            return False

        return True

    # netsh wlan show profiles interface="wi-fi"
    # netsh wlan connect ssid=default name=default
    def connect(self):
        if not self.supported_platform():
            return

        self.run_command(
            ["netsh", "wlan", "connect", f"ssid={self.ssid}", f"name={self.ssid}"],
            success_message=f"{self.interface.lower()} connected to {self.ssid}!",
        )

    # netsh interface ipv4 show config wi-fi
    def get_dns(self):
        if not self.supported_platform():
            return

        output = self.run_command(
            ["netsh", "interface", "ipv4", "show", "config", self.interface],
            success_message=f"retrieved dns configuration for {self.interface.lower()}.",
        )

        if output:
            logging.info(f"current dns configuration:\n{output}")

    # netsh interface ipv4 set dns wi-fi dhcp
    def reset_dns(self):
        if not self.supported_platform():
            return

        self.run_command(
            ["netsh", "interface", "ipv4", "set", "dns", self.interface, "dhcp"],
            success_message=f"reset {self.interface} dns settings to automatic.",
        )

    # netsh interface ipv4 set dns wi-fi static 127.0.0.1 validate=no
    def set_dns(self, primary_dns="127.0.0.1"):
        if not self.supported_platform():
            return

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
