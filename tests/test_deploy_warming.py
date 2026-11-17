import os
import unittest
from unittest.mock import patch

import site_autorefresh as refresh


class DeployWarmingTests(unittest.TestCase):
    def test_new_release_does_not_rebuild_all_old_pages(self):
        state = {"code": "previous"}
        with patch.dict(os.environ, {"PL_EAGER_WARM": "0"}), \
                patch.object(refresh, "_RUNNING_CODE", "new"), \
                patch.object(refresh, "_save_state"), \
                patch.object(refresh, "_rebuild_page") as rebuild, \
                patch.object(refresh, "_refresh_sport_picks") as picks:
            refresh._rebuild_for_new_code(object(), "unused.db", state)
        rebuild.assert_not_called()
        picks.assert_not_called()
        self.assertEqual(state["code"], "new")

    def test_normal_changed_results_are_still_updated(self):
        stamp = ["2099-01-01", 4]
        state = {}
        app = object()
        with patch.object(refresh, "_finals_stamp", return_value={"NBA": stamp}), \
                patch.object(refresh, "_saved_pages", return_value=[("", "")]), \
                patch.object(refresh, "_rebuild_page", return_value=True) as rebuild, \
                patch.object(refresh, "_page_missing_night", return_value=False), \
                patch.object(refresh, "_refresh_sport_picks") as picks, \
                patch.object(refresh, "_save_state"), \
                patch.object(refresh, "PAGE_PAUSE_S", 0), \
                patch.object(refresh, "_tried_pages", set()):
            refresh._refresh_results(app, "unused.db", state)
        rebuild.assert_called_once_with(app, "NBA", "", "")
        picks.assert_called_once_with("NBA")
        self.assertEqual(state["rebuilt"]["NBA"], stamp)


if __name__ == "__main__":
    unittest.main()
