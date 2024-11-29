import logging
import platform
import subprocess


class Adapter:
    # https://answers.microsoft.com/en-us/windows/forum/all/solved-unable-to-stop-internet-connection-sharing/b01e1ebc-4f9d-4bf6-8d15-37a782fa03ff
    # use this 'netstat -ab -p udp' to find listener

    def __init__(self, config):
        self.interface = config.interface
        self.ssid = config.ssid

    def supported_platform(self):
        supported = False
        os = platform.system().lower()

        if os == "windows":
            supported = True
        else:
            logging.error(f"error unsupported platform, {os}.")

        return supported

    # netsh wlan show profiles interface="wi-fi"
    # netsh wlan connect ssid=default name=default
    def connect(self):
        if not self.supported_platform():
            return

        try:
            result = subprocess.run(
                ["netsh", "wlan", "connect", f"ssid={self.ssid}", f"name={self.ssid}"],
                capture_output=True,
                text=True,
                check=True,
            )

            logging.info(f"{self.interface.lower()} connected to {self.ssid}!")

        except subprocess.CalledProcessError as err:
            logging.error(f"unexpected {err=}, {type(err)=}")

    # netsh interface ipv4 show config wi-fi
    def get_dns(self):
        if not self.supported_platform():
            return

        try:
            result = subprocess.run(
                ["netsh", "interface", "ipv4", "show", "config", self.interface],
                capture_output=True,
                text=True,
                check=True,
            )

            logging.info(
                f"current {self.interface.lower()} dns configuration:\n{result.stdout}"
            )

        except subprocess.CalledProcessError as err:
            logging.error(f"unexpected {err=}, {type(err)=}")

    # netsh interface ipv4 set dns wi-fi dhcp
    def reset_dns(self):
        if not self.supported_platform():
            return

        try:
            subprocess.run(
                ["netsh", "interface", "ipv4", "set", "dns", self.interface, "dhcp"],
                check=True,
            )

            logging.info(f"reset {self.interface} dns settings to automatic.")

        except subprocess.CalledProcessError as err:
            logging.error(f"unexpected {err=}, {type(err)=}")

    # netsh interface ipv4 set dns wi-fi static 127.0.0.1 validate=no
    def set_dns(self, primary_dns="127.0.0.1"):
        if not self.supported_platform():
            return

        try:
            subprocess.run(
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
                check=True,
            )

            logging.info(f"set {self.interface} primary dns set to {primary_dns}.")

        except subprocess.CalledProcessError as err:
            logging.error(f"unexpected {err=}, {type(err)=}")
