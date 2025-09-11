#! /bin/bash

cd $HOME/projects/github/poor-man-dns

source $HOME/projects/venv/bin/activate

authbind --deep $HOME/projects/venv/bin/python3 -uX dev app/main.py
