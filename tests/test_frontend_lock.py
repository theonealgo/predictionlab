from scripts.verify_frontend_lock import main


def test_approved_frontend_files_are_locked():
    assert main() == 0
