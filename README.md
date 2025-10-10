# poor-man-dns: a simple, lightweight dns and doh server


## overview

`poor-man-dns` is a simple, lightweight tool designed to secure your local dns and enhance privacy.
it serves as either a dns server or a doh (dns-over-https) server or both, protecting against malicious dns lookups.
compared to more complex solutions, it offers an accessible alternative for basic security needs.


## what it can do, features

it can be configured as:
1. **dns server**: provides secure dns resolution.
2. **doh server**: offers encrypted dns resolution over secure https connections.
3. **dual dns and doh server**: provides both dns and doh server functionality.
4. **ddns server**: provides ddns resolution.

## where it can be used, usages

here are some example scenarios:
1. **public wi-fi hotspots**: safeguards against malicious domains.
2. **heavy browsing sites**: pProtects against slow or untrusted domains used for gaming, streaming, etc.
3. **privacy concerns**: enhances security in everyday browsing.


## how to use

1. the easiest and simplist:
   * ensure you have windows operating system
   * download the executable, extract, and run it.

2. advance usage:
   * git clone the source


## advance usage

1. ensure these pre-requisites have been set-up:
   * windows operating system
   * python v3.12
   * git

2. git clone the poor-man-dns-repo:
   ```
   git clone https://github.com/hongks/poor-man-dns.git
   ```

3. create the python virtual environment:
   ```
   cd poor-man-dns
   python -m venv venv
   ```

4. install the python dependecies:
   ```
   venv/scripts/activate
   pip install -Ur requirements.txt
   ```

5. create the poor-man-dns config file:
   ```
   python -u app/main.py -dg
   ```

6. configure the poor-man-dns config file:
   ```
   notepad run/config.yml
   ```

7. run the poor-man-dns:
   ```
   python -u app/main.py -d
   ```

8. update repo / pips:
   ```
   git pull origin
   pip install -Ur requirements.txt
   ```


## configuration

1. check the poor-man-dns/run/config.xml.
2. to configure wifi, commands are easier. open the command prompt in administrator mode:

   * get the wifi ssid, and update adapter > ssid in the config.xml:
      ```
      netsh wlan show profiles interface="wi-fi"
      ```

   * set up the default, global wifi interface to use local dns:
      ```
      netsh interface ipv4 set dns wi-fi static 127.0.0.1 validate=no
      ```
      this is the same as adapter > enable in the config.xml.

   * ensure wifi interface change successful:
      ```
      netsh interface ipv4 show config wi-fi
      ```

   * reset the wifi interface to default dns configuration:
      ```
      netsh interface ipv4 set dns wi-fi dhcp
      ```
      this is the same as adapter > reset_on_exit in the config.xml.

   * to list all interfaces and its dns configuration:
      ```
      netsh interface ipv4 show dnsserver
      ```
      you can replace the "wi-fi" with "ethernet" if needed. use "" if the interface name has space.


## frequently asked questions
1. linux compatibility, possible:
   * tested in ubuntu, but need to disable the network manager.

2. dockerfile available:
   * tested in ubuntu, but need to disable the network manager.

3. console support:
   ```
   python -u app/main.py -dh
   ```
4. dynamic dns support:
   * experimental

5. dns-over-tls support:
   * experimental

6. forward zone support:
   * experimental

7. web frontend:
   * experimental


## troubleshooting

1. executable file:
   * in most cases, removing the **cache.sqlite**, and re-run the executable will sort the issue.

2. git clone:
   * pull the latest version, and re-run.

