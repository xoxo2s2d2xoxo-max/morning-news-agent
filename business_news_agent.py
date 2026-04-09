#!/usr/bin/env python3
"""
Japan major news briefing agent.

- Collects major Japanese headlines from RSS feeds
- Creates a concise Japanese morning briefing with Claude AI summary
- Sends the briefing via Gmail
- Runs once or every day at 05:00 (Asia/Tokyo)
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import smtplib
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from zoneinfo import ZoneInfo
