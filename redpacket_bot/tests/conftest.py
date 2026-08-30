# -*- coding: utf-8 -*-
"""Test ortamı: redpacket_bot kökünü sys.path'e ekler, veriyi geçici dizine yönlendirir."""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

# testler sırasında veri dosyaları repo dışında kalsın
os.environ.setdefault("RBP_TEST", "1")
