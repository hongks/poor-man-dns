# Introduction

poor-man-dns: secure your dns and protect your devices on windows. the pi-hole alternative.

&nbsp;

## Getting Started

just make sure you have the latest python v3.11 installed and configured in your Windows operating system.

&nbsp;

## Installation

no installation required, designed for portability!

&nbsp;

## Usage

download the executable file over at the release page.
just run it.

&nbsp;

## Advance Usage


1. git clone the poor-man-dns-repo:

   ```
   $ git clone https://github.com/hongks/poor-man-dns.git
   ```

2. create the python virtual environment:

   ```
   $ cd poor-man-dns
   $ python -m venv venv
   ```

3. install the python dependecies:

   ```
   $ venv/script/activate
   $ pip install -r requirements.txt
   ```

4. run the poor-man-dns:

   ```
   $ cd run
   $ python -u ../app/main.py
   ```

&nbsp;

## Configuration

to-be-continued

&nbsp;

## Troubleshooting

in most case, remove the **cache.sqlite** and re-run the executable will be fine.

&nbsp;
