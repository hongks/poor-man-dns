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
   ```
   * windows operating system
   * python v3.12
   * git
   ```

2. git clone the poor-man-dns-repo:
   ```
   $ git clone https://github.com/hongks/poor-man-dns.git
   ```

3. create the python virtual environment:
   ```
   $ cd poor-man-dns
   $ python -m venv venv
   ```

4. Install the python dependecies:
   ```
   $ venv/scripts/activate
   $ pip install -r requirements.txt
   ```

5. run the poor-man-dns:
   ```
   $ cd run
   $ python -u ../app/main.py
   ```

6. update repo / pips:
   ```
   $ git pull origin
   $ pip install --upgrade -r requirements.txt
   ```


## configuration

check the poor-man-dns/run/config.xml.


## frequently asked questions
1. linux compatibility, possible:
   * tested in ubuntu, but need to disable the network manager.

2. dockerfile available.
   * possible, but not tested.


## troubleshooting

1. executable file:
   * in most cases, removing the **cache.sqlite**, and re-run the executable will help out.

2. git clone:
   * pull the latest version, and re-run.

