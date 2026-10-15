"""Recognize live run activities without treating paused-history text as a run."""
import unittest

import login_support as login


PACKAGE = 'cn.edu.aiit.axx'


def record(component='.activity.SportMapActivity', package=PACKAGE,
           task=66, finishing=False, token='ab12cd'):
    flags = ' f' if finishing else ''
    return f'ActivityRecord{{{token} u0 {package}/{component} t{task}{flags}}}'


class ActivityGuardTests(unittest.TestCase):
    def test_all_supported_resumed_sources_block_an_open_run(self):
        for source in ('mResumedActivity:', 'topResumedActivity=',
                       'ResumedActivity:', 'Resumed:'):
            with self.subTest(source=source):
                self.assertTrue(login.has_open_run_activity(f'    {source} {record()}'))

    def test_full_activity_class_and_abbreviated_class_are_recognized(self):
        for component in ('.activity.SportMapActivity',
                          PACKAGE + '.activity.SportMapActivity'):
            with self.subTest(component=component):
                self.assertTrue(login.has_open_run_activity(
                    f'    mResumedActivity: {record(component=component)}'))

    def test_background_hist_run_remains_open_while_web_is_resumed(self):
        dump = '\n'.join([
            f'    mResumedActivity: {record(component=".activity.WebActivity")}',
            f'      * Hist #1: {record()}',
            '          state=STOPPED stopped=true finishing=false',
            '          keysPaused=false inHistory=true',
        ])
        self.assertTrue(login.has_open_run_activity(dump))

    def test_background_activities_list_does_not_require_foreground_visibility(self):
        dump = '\n'.join([
            f'    mResumedActivity: {record(package="com.android.settings", component=".Settings")}',
            f'      Activities=[{record(component=".activity.HomeActivity")}, {record()}]',
        ])
        self.assertTrue(login.has_open_run_activity(dump))

    def test_star_activity_record_source_is_recognized(self):
        self.assertTrue(login.has_open_run_activity(f'    * {record()}'))

    def test_only_exact_package_and_activity_component_match(self):
        for package, component in (
                ('com.other.app', '.activity.SportMapActivity'),
                (PACKAGE + '.clone', '.activity.SportMapActivity'),
                (PACKAGE, '.activity.SportMapActivityExtra'),
                (PACKAGE, '.other.SportMapActivity'),
                (PACKAGE, '.activity.NotSportMapActivity'),
                (PACKAGE, 'other.package.activity.SportMapActivity')):
            with self.subTest(package=package, component=component):
                self.assertFalse(login.has_open_run_activity(
                    f'    mResumedActivity: {record(package=package, component=component)}'))

    def test_last_paused_reference_is_not_a_live_record(self):
        for task, finishing in ((66, False), (-1, True)):
            with self.subTest(task=task, finishing=finishing):
                dump = '\n'.join([
                    f'    mResumedActivity: {record(component=".activity.WebActivity")}',
                    f'    mLastPausedActivity: {record(task=task, finishing=finishing)}',
                    f'      Activities=[{record(component=".activity.WebActivity")}]',
                ])
                self.assertFalse(login.has_open_run_activity(dump))

    def test_unrecognized_reference_sources_are_ignored(self):
        for line in (
                f'    mLastResumedActivity: {record()}',
                f'    mLastPausedActivity: {record()}',
                f'    previousActivity={record()}',
                f'    intent={{cmp={PACKAGE}/.activity.SportMapActivity}}',
                f'    mActivityComponent={PACKAGE}/.activity.SportMapActivity',
                f'    windows=[Window{{a1 u0 {PACKAGE}/{PACKAGE}.activity.SportMapActivity}}]',
                'SportMapActivity',
                'background SportMapActivity'):
            with self.subTest(line=line):
                self.assertFalse(login.has_open_run_activity(line))

    def test_detached_or_finishing_record_is_ignored_in_every_supported_source(self):
        wrappers = (
            lambda value: '    mResumedActivity: ' + value,
            lambda value: '    topResumedActivity=' + value,
            lambda value: '    ResumedActivity: ' + value,
            lambda value: '    Resumed: ' + value,
            lambda value: '      Activities=[' + value + ']',
            lambda value: '      * Hist #0: ' + value,
            lambda value: '      * ' + value,
        )
        for wrapper in wrappers:
            for task, finishing in ((-1, False), (66, True), (-1, True)):
                line = wrapper(record(task=task, finishing=finishing))
                with self.subTest(line=line):
                    self.assertFalse(login.has_open_run_activity(line))

    def test_finishing_record_in_list_does_not_hide_later_live_record(self):
        dump = f'      Activities=[{record(finishing=True)}, {record(token="feed42")}]'
        self.assertTrue(login.has_open_run_activity(dump))

    def test_detached_record_in_list_does_not_hide_later_live_record(self):
        dump = f'      Activities=[{record(task=-1)}, {record(task=67)}]'
        self.assertTrue(login.has_open_run_activity(dump))

    def test_other_package_record_in_list_does_not_hide_later_live_record(self):
        dump = f'      Activities=[{record(package="com.other.app")}, {record()}]'
        self.assertTrue(login.has_open_run_activity(dump))

    def test_mixed_history_and_finished_records_do_not_block_result_page(self):
        web = record(component='.activity.WebActivity')
        dump = '\n'.join([
            'ACTIVITY MANAGER ACTIVITIES (dumpsys activity activities)',
            'Display #0 (activities from top to bottom):',
            f'    mResumedActivity: {web}',
            f'    mLastPausedActivity: {record(task=-1, finishing=True)}',
            f'      Activities=[{record(finishing=True)}, {web}]',
            f'      * Hist #1: {record(task=-1)}',
            f'      * Hist #0: {web}',
            f'          Intent {{ cmp={PACKAGE}/.activity.SportMapActivity }}',
        ])
        self.assertFalse(login.has_open_run_activity(dump))

    def test_hex_token_containing_f_is_not_a_finishing_flag(self):
        self.assertTrue(login.has_open_run_activity(
            f'      * Hist #0: {record(token="fabcff")}'))

    def test_empty_or_null_activity_dump_has_no_live_record(self):
        for dump in ('', '  \n', '    mResumedActivity: null\nActivities=[]'):
            with self.subTest(dump=dump):
                self.assertFalse(login.has_open_run_activity(dump))


if __name__ == '__main__':
    unittest.main()
