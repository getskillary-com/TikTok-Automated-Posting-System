from __future__ import annotations

import unittest

from mobile_phone_library.windows_device_names import extract_serial, parse_wpd_name_rows


class WindowsDeviceNameTests(unittest.TestCase):
    def test_parse_wpd_rows_maps_parent_serial_to_friendly_name(self) -> None:
        raw = """
        [
          {
            "serial": "SERIALSAMSUNG01",
            "friendly_name": "Galaxy A52 5G",
            "parent": "USB\\\\VID_04E8&PID_6860\\\\SERIALSAMSUNG01"
          },
          {
            "serial": "SERIALREDMI01",
            "friendly_name": "Redmi Note 13 Pro 5G",
            "parent": "USB\\\\VID_2717&PID_FF48\\\\SERIALREDMI01"
          }
        ]
        """

        names = parse_wpd_name_rows(raw)

        self.assertEqual(names["serialsamsung01"], "Galaxy A52 5G")
        self.assertEqual(names["serialredmi01"], "Redmi Note 13 Pro 5G")

    def test_extract_serial_from_parent_instance_id(self) -> None:
        self.assertEqual(extract_serial(r"USB\VID_2717&PID_FF48\SERIALREDMI01"), "SERIALREDMI01")


if __name__ == "__main__":
    unittest.main()
