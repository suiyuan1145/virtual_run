import unittest
from unittest.mock import patch
import android_location_test as app
from location_tools import (align_app_location, baidu_to_wgs, distance_meters,
                            parse_app_location, parse_gps_location)


class LocationRegressionTests(unittest.TestCase):
    def test_cross_continent_start_explains_reported_distance(self):
        distance = distance_meters((37.421998, -122.084), (31.2304, 121.4737)) / 1000
        self.assertTrue(9900 < distance < 10000)

    def test_gps_selects_fine_gps_instead_of_stale_passive_or_coarse(self):
        text = ('    passive provider:\n      last location=Location[gps 37.4,-122 hAcc=5]\n'
                '    network provider:\n      last location=null\n'
                '    gps provider:\n      last coarse location=Location[gps 37.4,-122 hAcc=2000]\n'
                '      last location=Location[gps 31.230432,121.474042 hAcc=5]\n')
        self.assertEqual(parse_gps_location(text), (31.230432, 121.474042))

    def test_missing_gps_does_not_use_coarse_or_network_coordinates(self):
        for text in ('last coarse location=Location[gps 37,-122]',
                     'last location=Location[network 31,121]',
                     '    gps provider:\n      last location=null\n'):
            with self.subTest(text=text), self.assertRaises(RuntimeError):
                parse_gps_location(text)

    def test_read_latest_app_coordinate_and_reject_stale_logs(self):
        logs = ('1000.000 E/sport   ( 5328): 37.421998/-122.084\n'
                '1010.125 E/sport   ( 5328): 31.23434/121.485132\n')
        self.assertEqual(parse_app_location(logs, 1012)[:2], (31.23434, 121.485132))
        with self.assertRaises(RuntimeError):
            parse_app_location(logs, 1040)
        actual_format = '         1791449579.283  5328  5328 E sport   : 31.23434/121.485132\n'
        self.assertEqual(parse_app_location(actual_format, 1791449580)[:2], (31.23434, 121.485132))

    def test_observed_sdk_baidu_coordinate_uses_exact_matching_gps(self):
        gps = (31.230432, 121.474042)
        result = align_app_location(31.23434, 121.485132, gps)
        self.assertEqual(result[:2], gps)
        self.assertLess(distance_meters(baidu_to_wgs(31.23434, 121.485132), gps), 2)

    def test_overseas_sdk_coordinate_keeps_wgs84(self):
        gps = (37.421998, -122.084)
        self.assertEqual(baidu_to_wgs(*gps), gps)

    def test_velocity_unit_and_stopped_velocity(self):
        wire = app.location_command(31, 121, 8)
        self.assertEqual(wire[3:5], ['121.00000000', '31.00000000'])
        self.assertAlmostEqual(float(wire[-1]) * 1.852, 8, places=5)
        self.assertEqual(float(app.location_command(31, 121, 0)[-1]), 0)
        with patch.object(app, 'adb', return_value='OK') as mocked:
            app.send_location('emulator-5554', 31, 121, 6)
            self.assertEqual(mocked.call_args.args[0], app.location_command(31, 121, 6))

    def test_speed_is_real_time_and_circle_remains_local(self):
        p = app.circle_position(31, 121, 150, 6 / 3.6 * 30)
        self.assertAlmostEqual(distance_meters((31, 121), p[:2]), 49.7688, delta=.1)


if __name__ == '__main__':
    unittest.main()
