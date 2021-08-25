from kamalib.kl import KamakuraLibrary
from unittest import TestCase

import json

class TestLogic(TestCase):
    def test_json_upload(self):
        with open("./tests/books.json") as f:
            data = json.load(f)
            kl = KamakuraLibrary()
            kl.upload(data)
